"""CBAM-GSC dynamic core and synthetic structural validation.

本包实现《详细模型建模方案与数据需求》§17.1 的模型/分析模块骨架，
    并支持两地区、2027—2029及2026—2040的合成结构案例运行
N（无边境措施）、C（CBAM）、G(κ)（证书替代）情景比较。

实例数值全部为 scenario 玩具值（TOY_STAGE_A），不是校准数据；
结果只用于结构检查（§17.2 阶段A），不能解读为政策效果。
"""
