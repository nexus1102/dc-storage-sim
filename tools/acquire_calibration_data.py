"""Bounded acquisition for offline calibration, not a simulator feature.
Raw files stay in ignored external/. Never unpickle or execute downloaded data.
HUST: read ZIP index and two fixed members via HTTP ranges, not the 1.2GB ZIP.
"""
import hashlib
import io
import json
from pathlib import Path
import struct
import time
import zipfile
import zlib
import requests

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'external/empirical_20261111'
SESSION=requests.Session()


def fetch(url,limit=8_000_000,headers=None):
    started=time.monotonic()
    with requests.get(url,headers=headers,stream=True,timeout=(10,30)) as r:
        r.raise_for_status();data=bytearray()
        for chunk in r.iter_content(1<<18):
            data.extend(chunk)
            if len(data)>limit or time.monotonic()-started>120:raise ValueError('bounded download exceeded')
        return bytes(data)


def record_file(path,data,url,**metadata):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and path.read_bytes()!=data:raise ValueError('refuse to overwrite different acquired bytes: '+str(path))
    if not path.exists():path.write_bytes(data)
    return dict(path=str(path.relative_to(ROOT)),url=url,size=len(data),sha256=hashlib.sha256(data).hexdigest(),**metadata)


def byte_range(url,start,count):
    if start<0 or count<=0 or count>30_000_000:raise ValueError('invalid bounded range')
    if count>2_000_000:
        return b''.join(byte_range(url,start+k,min(2_000_000,count-k)) for k in range(0,count,2_000_000))
    for attempt in range(2):
        try:
            with SESSION.get(url,headers={'Range':f'bytes={start}-{start+count-1}','Accept-Encoding':'identity'},stream=True,timeout=(10,30)) as r:
                if r.status_code!=206 or not r.headers.get('Content-Range','').startswith(f'bytes {start}-{start+count-1}/'):
                    raise ValueError('server did not honor requested range')
                data=r.raw.read(count+1)
                if len(data)!=count:raise ValueError(f'range byte count mismatch: {len(data)} vs {count}')
                return data
        except (requests.RequestException,OSError):
            if attempt:raise ValueError('public source connection failed twice; partial bytes retained') from None
            time.sleep(1)


class ZipIndex(io.RawIOBase):
    def __init__(self,url,size):self.url=url;self.size=size;self.pos=0;self.total=0
    def seekable(self):return True
    def tell(self):return self.pos
    def seek(self,offset,whence=0):
        self.pos=offset if whence==0 else self.pos+offset if whence==1 else self.size+offset
        return self.pos
    def read(self,n=-1):
        n=self.size-self.pos if n<0 else min(n,self.size-self.pos)
        if n<=0:return b''
        if self.total+n>2_000_000:raise ValueError('ZIP index budget exceeded')
        data=byte_range(self.url,self.pos,n);self.pos+=n;self.total+=n
        return data


def acquire():
    records=[];DEST.mkdir(parents=True,exist_ok=True)
    for repo,branch,folder,paths in [
        ('Azure/AzurePublicDataset','215becdacba1ce682c7368642ce97d5a332de7a6','azure',['AzureLLMInferenceDataset2023.md','LICENSE','data/AzureLLMInferenceTrace_code.csv','data/AzureLLMInferenceTrace_conv.csv']),
        ('mlcommons/inference_results_v5.1','5ea4f62ef62536e6bf4d78a9b440fb9035ddfb4a','mlperf',['LICENSE.md','summary_results.json','closed/Lenovo/results/SR680a_V3_B200SXMx8_TRT/llama2-70b-99/Server/performance/run_1/mlperf_log_summary.txt'])]:
        sha=json.loads(fetch(f'https://api.github.com/repos/{repo}/commits/{branch}'))['sha']
        for name in paths:
            url=f'https://raw.githubusercontent.com/{repo}/{sha}/{name}'
            # Preserve original names; earlier exploration used different log name.
            record=record_file(DEST/folder/Path(name).name,fetch(url),url,repository=repo,commit=sha)
            records.append(record)
    endpoint='https://data.mendeley.com/public-api/datasets/nsc7hnsg4s/files?folder_id=root&version=2&$start=0&$limit=1000'
    files=json.loads(fetch(endpoint));archive=next(x for x in files if x['filename']=='our_data.zip')
    url=archive['content_details']['download_url'];size=archive['size']
    with zipfile.ZipFile(ZipIndex(url,size)) as z:
        for name in ('our_data/1-1.pkl','our_data/1-2.pkl'):
            info=z.getinfo(name)
            if info.file_size>100_000_000 or info.compress_size>30_000_000:raise ValueError('cell exceeds acquisition budget')
            header=byte_range(url,info.header_offset,30)
            if header[:4]!=b'PK\x03\x04':raise ValueError('invalid local ZIP header')
            fn,extra=struct.unpack_from('<HH',header,26)
            compressed=byte_range(url,info.header_offset+30+fn+extra,info.compress_size)
            if info.compress_type!=zipfile.ZIP_DEFLATED:raise ValueError('unexpected compression method')
            d=zlib.decompressobj(-15);raw=d.decompress(compressed,info.file_size+1)
            if not d.eof or len(raw)!=info.file_size or zlib.crc32(raw)!=info.CRC:raise ValueError('member integrity mismatch')
            records.append(record_file(DEST/'hust'/Path(name).name,raw,url,member=name,
                dataset_doi='10.17632/nsc7hnsg4s.2',license='CC-BY-4.0',zip_crc32=info.CRC,
                archive_expected_sha256=archive['content_details']['sha256_hash'],full_archive_hash_verified=False,
                note='only selected member ranges downloaded; member CRC and extracted SHA checked'))
            print('Acquired '+name,flush=True)
    manifest=dict(date='2026-11-11',scope='external public measurements; not target-site calibration',records=records)
    path=DEST/'acquisition.json';path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(str(path),flush=True)
    return path


def acquire_azure_week():
    """One complete public release asset, hard size/time budget; no synthetic days."""
    name='AzureLLMInferenceTrace_code_1week.csv'
    endpoint='https://api.github.com/repos/Azure/AzurePublicDataset/releases/tags/dataset-llm-2024'
    release=json.loads(fetch(endpoint));asset=next(a for a in release['assets'] if a['name']==name)
    if asset['size']>720_000_000:raise ValueError('release asset exceeds fixed budget')
    path=DEST/'azure'/name;path.parent.mkdir(parents=True,exist_ok=True)
    written=path.stat().st_size if path.exists() else 0
    if written>asset['size']:raise ValueError('existing file exceeds release size')
    if written and written<asset['size']:
        # Resume only an identical public-asset prefix; never overwrite/delete.
        n=min(written,1_000_000)
        with path.open('rb') as f:prefix=f.read(n)
        if prefix!=byte_range(asset['browser_download_url'],0,n):raise ValueError('existing prefix differs; manual review needed')
    started=time.monotonic()
    if written<asset['size']:
        with path.open('ab') as f:
            while written<asset['size']:
                if time.monotonic()-started>600:raise ValueError('release download time budget exceeded')
                chunk=byte_range(asset['browser_download_url'],written,min(2_000_000,asset['size']-written))
                f.write(chunk);written+=len(chunk)
                if written//100_000_000!=(written-len(chunk))//100_000_000:print(f'Azure bytes acquired: {written}/{asset["size"]}',flush=True)
    digest=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20),b''):digest.update(chunk)
    expected=asset.get('digest')
    if expected and expected!='sha256:'+digest.hexdigest():raise ValueError('published asset digest mismatch')
    record=dict(path=str(path.relative_to(ROOT)),url=asset['browser_download_url'],size=asset['size'],sha256=digest.hexdigest(),release_id=release['id'],asset_id=asset['id'],published_digest=expected,license='CC-BY-4.0',status='downloaded_complete')
    (DEST/'azure/week_acquisition.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
    print('Acquired complete Azure 2024 asset: '+str(path),flush=True)
    return record

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--azure-week',action='store_true');args=ap.parse_args()
    acquire_azure_week() if args.azure_week else acquire()
