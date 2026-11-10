"""Build explicit provenance and sensitivity bounds for every v3 configuration field."""
from dataclasses import fields
import pandas as pd
from config import Config,ROOT

TARIFF_URL='https://www.jintan.gov.cn/html/czjt/2025/JFKEQAOD_0624/281022.html'
POLICY_URL='https://fzggw.jiangsu.gov.cn/art/2025/4/30/art_284_11557001.html'
MARKET_URL='https://jsb.nea.gov.cn/dtyw/jgdt/202605/t20260508_300757.html'

def table(cfg=None):
    c=cfg or Config(); rows=[]
    units={'dt':'h','ride_h':'h','reserve_h':'h','reg_duration':'h','grid_max':'MW','ac_max':'MW',
           'demand_rate':'CNY/MW/day','reg_capacity_price':'CNY/MW/h','reg_mileage_price':'CNY/MW-mileage',
           'thermal_ua':'MW/K','thermal_c':'MWh/K','thermal_range':'K','thermal_tmax':'degC',
           'thermal_ambient':'degC','capex_kwh':'CNY/kWh','penalty':'CNY/MWh'}
    rationale={
        'eta':'双向对称效率工程假设；未获得本项目设备效率曲线',
        'soc_max':'可用能量上界工程假设；未获得电池厂商SOC限值',
        'reserve_h':'CVaR尾均值时长近似固定为3分钟；与15分钟启动失败压力臂并列',
        'reg_capacity_price':'未获得2025版实施细则完整结算条款；保守设0，不沿用旧25元假设',
        'reg_mileage_price':'6为研究情景报价，不是江苏实测或政策定价；待实测结算数据替换',
        'mileage_ratio':'每小时8倍里程工程假设；trace无AGC信号',
        'performance':'性能系数1假设；无电站实测AGC跟踪',
        'activation_beta':'每小时每MW单向容量0.05MWh等效循环吞吐；无实测AGC',
        'thermal_ua':'等效围护传热系数假设；由一阶热平衡推导，不是校准热参数',
        'thermal_c':'等效机房热容假设；未取得楼宇热响应测量',
        'thermal_cop':'空调恒定COP假设，未拟合部分负载性能',
        'flex_scale':'业务可中断比例倍数；pod没有checkpoint能力标签',
        'window_scale':'截止窗口倍数；pod存活时长不能识别允许延迟',
        'recovery_scale':'恢复速率倍数；未取得业务服务率测量',
        'cycle_fade':'线性EFC衰减率假设；不同于PyBaMM三档代理成本，未做雨流计数',
        'calendar_fade':'年历衰减率假设；未做温度/日历寿命标定',
        'penalty':'备用/终端软约束罚率，表示不可执行代价，不是市场罚则',
    }
    for f in fields(c):
        value=getattr(c,f.name); kind='假设'; source='本研究配置 Config.'+f.name
        boundary=rationale.get(f.name,'本研究明确设定的工程/经济情景，须通过上下扰动解释结论')
        low,high=value*.8,value*1.2
        if f.name in ('flex_scale','window_scale','recovery_scale'): low,high=.5,1.5
        if f.name in ('eta','soc_max'): low,high=.9,.99
        if f.name=='reg_capacity_price': low,high=0.,25.
        if f.name=='discount': low,high=0.,.12
        if f.name=='replacement_soh': low,high=.7,.9
        if f.name=='dt': kind='配置'; low=high=value; boundary='来自输入的15分钟采样，不当作物理不确定性'
        if f.name=='seed': kind='配置'; low=high=value; boundary='可复现随机种子'
        if f.name=='accounting_version': kind='配置'; low=high=value; boundary='3为历史复核，4为修正计费与激活能量闭合；不是连续敏感性参数'
        if f.name=='years': low,high=10,20
        if f.name=='demand_rate':
            kind='政策'; source=TARIFF_URL; boundary='2025-06两部制1–10(20)kV 51.2元/kW/月，按30天摊销'
        rows.append(dict(parameter=f.name,value=value,unit=units.get(f.name,'1'),source_type=kind,
                         source=source,low=low,high=high,boundary=boundary))
    for key,value,unit,source,boundary in [
        ('tariff_peak',991.9,'CNY/MWh',TARIFF_URL,'历史2025-06代理购电；非2026现货预测'),
        ('tariff_flat',649.9,'CNY/MWh',TARIFF_URL,'同一电压等级、月份'),
        ('tariff_valley',372.,'CNY/MWh',TARIFF_URL,'同一电压等级、月份'),
        ('tariff_periods',20250601,'YYYYMMDD',POLICY_URL,'苏发改价格发〔2025〕426号，2025-06-01执行；夏季时段'),
        ('degradation_calibration',22.462056,'CNY/MWh','data/inputs/degradation_calibration.csv','PyBaMM仿真标定，不是实测；DoD<0.3最近档外推')]:
        rows.append(dict(parameter=key,value=value,unit=unit,source_type='仿真标定' if key=='degradation_calibration' else '政策',source=source,low=value,high=value,boundary=boundary))
    from business import BUSINESS
    for key,spec in BUSINESS.items():
        for attr in ('f','window_h','ramp_frac'):
            value=getattr(spec,attr)
            rows.append(dict(parameter=f'business_{key}_{attr}',value=value,unit='h' if attr=='window_h' else '1',
                             source_type='假设',source=f'sim/business.py:BUSINESS[{key}].{attr}',
                             low=.5*value,high=min(1.,1.5*value) if attr=='f' else 1.5*value,
                             boundary='pod轨迹无checkpoint/截止/恢复率标签；各业务参数独立±50%扫描'))
    return pd.DataFrame(rows)

if __name__=='__main__':
    table().to_csv(ROOT/'docs/parameter_provenance.csv',index=False,encoding='utf-8-sig')
