"""Read-only project classification. Writes review CSV/Markdown; never deletes files."""
import argparse
import csv
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def size(path):
    if path.is_file(): return 1,path.stat().st_size
    files=[p for p in path.rglob('*') if p.is_file()]
    return len(files),sum(p.stat().st_size for p in files)

def evidence_runs(root):
    needed={}
    for published in (root/'results').glob('*/PUBLISHED.json'):
        data=json.loads(published.read_text(encoding='utf-8'))
        if data.get('latest_run'): needed.setdefault(data['latest_run'],[]).append(str(published.relative_to(root)))
    for path in (root/'baseline/v3_regression_evidence.json',root/'evidence/v3_delivery.json'):
        if not path.is_file(): continue
        data=json.loads(path.read_text(encoding='utf-8'))
        for name in data.get('runs',[])+data.get('regression',{}).get('runs',[]):
            needed.setdefault(Path(name.replace('\\','/')).name,[]).append(str(path.relative_to(root)))
        for stage in data.get('stages',[]):
            needed.setdefault(Path(stage['run'].replace('\\','/')).name,[]).append(str(path.relative_to(root)))
    for path in (root/'runs').glob('v3delivery_*/SEAL.json'):
        needed.setdefault(path.parent.name,[]).append('已封存交付原件')
    for path in (root/'runs').glob('v3mpc_*/SEAL.json'):
        needed.setdefault(path.parent.name,[]).append('MPC验收原件')
    def referenced_runs(value):
        if isinstance(value,dict):
            for item in value.values():yield from referenced_runs(item)
        elif isinstance(value,list):
            for item in value:yield from referenced_runs(item)
        elif isinstance(value,str) and value.replace('\\','/').startswith('runs/'):
            parts=value.replace('\\','/').split('/')
            if len(parts)>1:yield parts[1]
    for path in (root/'evidence').glob('*validation*.json'):
        data=json.loads(path.read_text(encoding='utf-8'))
        for run in set(referenced_runs(data)):
            needed.setdefault(run,[]).append(str(path.relative_to(root)))
    return needed

def classify(relative,needed):
    p=Path(relative); parts=p.parts; name=p.name
    if parts[0]=='.git': return 'GIT元数据','必须保留','删除会丢失版本历史、标签和本地身份配置'
    if parts[0]=='.venv': return '本机Python环境','重建后可手动删除','不入Git；删除后须重建.venv并安装依赖才能使用一键脚本'
    if '__pycache__' in parts or parts[0]=='.pytest_cache': return '自动缓存','可手动删除','运行/测试会重新生成，不是输入或验收原件'
    if parts[0]=='archive': return '历史归档/开发日志','检查后可手动删除','当前V3不依赖；原始代码快照可从Git标签v3-before-layout查看'
    if parts[0]=='replay': return '复算临时副本','检查后可手动删除','保留evidence中的复算摘要；删除仅失去现场复算目录'
    if parts[0]=='runs':
        run=parts[1]
        if run in needed: return '被引用的封存证据','建议保留证据','被引用：'+'; '.join(needed[run])+'；不影响新计算，但删除后对应封存复核不可用'
        seal=ROOT/'runs'/run/'SEAL.json'
        if seal.is_file(): return '未引用的历史封存','检查后可手动删除','已有SEAL，但当前发布/回归证据未引用；会丢失该次原件'
        return '未封存/失败运行','检查后可手动删除','无SEAL，不能作为已通过验收的运行'
    if parts[0]=='results': return '可再生输出','检查后可手动删除','新计算不读取旧输出作为输入；报告/发布副本校验需先重跑相应阶段'
    if parts[:2]==('data','inputs'): return 'V3必需输入','必须保留','日负荷与电池标定；已从results分离，受Git管理'
    if parts[:2]==('data','raw'):
        if name.startswith('openb_'): return 'V3原始数据','必须保留','阶段1业务统计及输入重建读取此数据'
        return '可选历史原始数据','按研究需要保留','Azure轨迹不参与当前V3；保留用于历史负荷复现'
    if parts[:2]==('sim','reference'): return 'V3验收参考内核','必须保留','五架构历史成本锚点需要，不能按旧版本垃圾删除'
    if parts[0] in ('sim','baseline','tests'): return 'V3代码/基线/测试','必须保留','完整计算、数值回归和工程验收依赖'
    if parts[0]=='tools': return '输入重建与维护工具','完整项目保留','主计算不调用；用于原始数据重建和目录复核'
    if parts[0]=='evidence': return '精简验收证据','建议保留证据','小型摘要受Git管理；保存历史验收/复算结论，不依赖开发日志'
    if parts[:2] in (('docs','research'),('docs','planning')): return '研究资料/开发要求','按研究需要保留','不参与数值求解，但不是可再生垃圾'
    if parts[0]=='docs': return '当前说明/溯源/维护清单','完整项目保留','parameter_provenance.csv为阶段冻结输入；其余解释架构、边界和清理影响'
    if name in ('README.md','NOTICE.md','requirements.txt','requirements-lock.txt','requirements-battery.txt','run_all.ps1','run_all.sh','pytest.ini','.gitignore','.gitattributes'):
        return '项目入口/依赖/规范','必须保留','安装、启动、测试、许可或Git管理需要'
    return '待人工确认','不要直接删除','尚未归入已知布局'

def inventory(root):
    needed=evidence_runs(root); rows=[]
    def append(path):
        relative=path.relative_to(root).as_posix(); category,decision,impact=classify(relative,needed)
        count,byte_count=size(path)
        rows.append(dict(path=relative,type='目录' if path.is_dir() else '文件',category=category,
                         review_decision=decision,file_count=count,bytes=byte_count,impact=impact))
    def walk(path):
        for p in sorted(path.iterdir(),key=lambda x:x.name):
            if p.name in ('.git','.venv','.pytest_cache','__pycache__'): append(p)
            elif p.is_dir() and p.parent.name in ('runs','replay'): append(p)
            elif p.is_dir():
                if not any(p.iterdir()):
                    rows.append(dict(path=p.relative_to(root).as_posix(),type='空目录',category='迁移后空目录',review_decision='可手动删除',file_count=0,bytes=0,impact='没有文件；Git不跟踪空目录'))
                else: walk(p)
            else: append(p)
    walk(root)
    return rows

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',default='docs/maintenance'); args=ap.parse_args()
    dest=ROOT/args.output; dest.mkdir(parents=True,exist_ok=True)
    rows=inventory(ROOT)
    with (dest/'file_inventory.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=rows[0]); writer.writeheader(); writer.writerows(rows)
    runs=[r for r in rows if r['path'].startswith('runs/')]
    with (dest/'run_review.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=rows[0]); writer.writeheader(); writer.writerows(runs)
    summary=['# 文件分类与手动清理总览','',
             '本工具只盘点，不执行删除。CSV包含每个普通文件，以及逐运行目录/缓存/环境的汇总。数字是本次快照；新运行或手动清理后请重新生成。','',
             '| 分类 | 条目数 | 文件数 | MiB |','|---|---|---|---|']
    for category in sorted({r['category'] for r in rows}):
        group=[r for r in rows if r['category']==category]
        summary.append(f"| {category} | {len(group)} | {sum(r['file_count'] for r in group)} | {sum(r['bytes'] for r in group)/1024**2:.2f} |")
    summary+=['','逐项判断见 [file_inventory.csv](file_inventory.csv)，逐运行保留判断见 [run_review.csv](run_review.csv)。',
              '', '完整运行所需内容：sim/（含reference/）、data/inputs/、data/raw/openb*.csv、baseline/、docs/parameter_provenance.csv、tests/、pytest.ini、依赖文件与启动脚本。',
              '当前平台不依赖archive/、replay/或以前生成的results/、runs/执行新计算。若要核验本次历史发布、哈希与验收原件，应保留run_review.csv中“建议保留证据”的目录。',
              '本机.venv约占大部分空间；这是执行环境，不是日志。删除后需重新安装。Git提交不包含.venv、archive、runs、replay或生成结果。']
    (dest/'文件分类与清理总览.md').write_text('\n'.join(summary)+'\n',encoding='utf-8')
    print(f'Classified {len(rows)} entries, {len(runs)} run directories. No files deleted.')

if __name__=='__main__': main()
