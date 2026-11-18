# CBAM与绿色钢铁证书动态模型

依据《详细模型建模方案与数据需求》实现的科研模型代码，默认政策版本为 `dynamic_core_v2`。真实2026初始状态尚未配平和校准。

研究期为2026—2040，合成示例采用三年建设期和两个地区。现有固定菜单按金属炉料质量计废钢比例，各配方自行记录物耗、排放、费用和工程约束。

| 路线 | 合成主例的配方废钢比 |
|---|---:|
| 高炉—转炉 | 12.5% |
| 共址氢基直接还原铁—电炉 | 0%、12.5%、25% |
| 全废钢电炉 | 100% |

各场景从同一组固定配方选择各自的产量，配方共用相应工序产能和资源，整家钢厂可同时运行多条配方。真实实例须自行编制表11、12和适用的合规及排放数据。Devlin的50%氢基配方可作扩展敏感性。主模型内生安排钢材、废钢与铁矿石去向，DR级球团独立建池。跨境HBI默认关闭，开放时适用于所有对照场景。

主情景最终钢材需求按单一粗钢当量 `STEEL_AGG` 处理；HBI是中间活动。`grade_id`保留以兼容历史实例和扩展，但不得把它误读为主模型板材需求。历史合成结果中的 `FLAT` 是旧标签，未据此生成新的政策结论。

## 已实现并验证

| 方案部分 | 本轮实现 | 边界 |
|---|---|---|
| §4 能源制氢 | 离网风光逐时优化和并网工业电价两种口径，交付上限、成本和排放可接入年度LP | 真实气象、工程成本和工业电价待校准；排放边界由输入因子决定 |
| §6 供需与物料 | 单一粗钢当量需求；DR球团供给块；固定配方铁平衡；共址热/冷DRI与跨境HBI；共享DRI/EAF和运输走廊 | 供给量、品位、杂质上限和物流按项目录入；废钢、矿石和球团用独立f变量，计发运损耗、费用、排放及运力约束 |
| §7 动态能力 | 按投运、寿命、明确退役年、scope_share和各自利用率计算初始资产；逐选项×决策年新建；建设滞后；共享年度建设上限 | 自然/已公告退役和闲置；未实现内生提前退出及改造，不接受将改造伪装为无源新建 |
| §8 排放账 | 责任、资格与物理工序边界分开；资源和运输单计；前体已在别处计量时不重复计工序排放/碳价 | 真实排放分量及其边界仍需校准 |
| §9 CBAM | 按商品类别截零；q/x/f入境映射；替代范围与保留范围现金义务分开；保留责任与共同关税进入各情景 | 不替代现行法规核验，不支持未提供的海关特殊程序 |
| §10 证书 | 逐年资格与认可量；逐年EU兼容规则；ρ比例及经证据链接的绝对权属上限；年内签发核销；出清对偶 | 不启用银行/现金退出；新项目专属资格须进一步链接实际生产批次，当前明确拒绝该选项 |
| §10 氢源核验 | 签发配方按实际氢资源ID及HBI物料池追溯到可核验可再生电力水电解；资格账加入制氢用电排放 | 合成示例中的证据标识仅供结构测试；CBAM责任和技术菜单继续独立处理 |
| §10 前体属性 | 当某配方已产生q映射的前体入境责任时，保守排除整条配方签发 | 这是研究主规则，并非对现行证书制度的法律认定 |
| §11 成本与价格 | 一次CAPEX、逐年FOM、保守残值；年度碳价；完整成本分项；逐年未折现现金流及PV对账；容量租金 | 使用采购成本代理，不自动声称资源福利；零义务年的对偶价格不可唯一识别 |
| §12 政策比较 | N、C及宽松、正常、严格三档G共同比较累计系统排放、欧盟产量及进口 | 三档变化可能通过生产投资和贸易重排影响排放 |
| §17 可复现 | 稀疏矩阵HiGHS；输入/代码哈希、Python/库版本、年份、数量单位、可行性残差、失败状态与比较表 | 成功求解不等于已完成真实校准 |

### 签发与出口商履约

1. 用废钢比加权的研究资格门槛 `0.40(1−s)+0.05s` 判断是否合格。用氢路线还须证明可追溯的可再生电力水电解。
2. 按产品和年份冻结基准。宽松档取C情景向欧盟交付中排放强度最高的半数交付量，求加权平均。正常档取C情景欧盟自产和进口的全部交付量加权平均。严格档取经核验、同口径的欧洲同类钢材低排放前10%参考值。
3. 每吨合格钢可提供 `max(0, 档位基准 − 责任参考排放强度)` 的签发额度。实际额度按兼容的交付量、可转让比例和核验权属上限确定。欧盟交付及已计入前体入境责任的属性按主规则排除。每个证书单位为1吨CO₂e的履约认可量。
4. G中出口商按本情景实际入境形成的CBAM等效净责任1:1采购并核销，资金进入属性供方。C按法定CBAM口径计算净责任，费用在模型中全部归给出口方，实际法定缴纳主体依适用规则。系统物理排放通过N、C及各G的实物活动另行比较。

### 严格绿氢的数据接口

- 表13中，氢块标记 `resource_class=h2`（也可用 `hydrogen`），使用 `hydrogen_production_method=water_electrolysis`、`renewable_power_evidence_id` 和 `electricity_emissions_tco2e_per_th2` 表达工艺、经审查的证据及用电排放因子。该字段以 tCO₂e/tH₂ 计。资格账自动计入这项排放，表19的资格分量只填其余适用排放。SYS继续使用表13按系统边界定义的 `emission_factor`。
- 在同一节点和年份，按氢源和用电排放强度拆分 `resource_id`（例如 `h2_renewable_a` 和 `h2_grid`）。表12的用氢配方使用对应的资源ID，产量消耗相应的供给上限。一个资源ID中的所有供给块须具有同一可核验来源类别和用电排放因子，才能作为合格氢源。
- 对使用氢基HBI的钢材，表12通过 `hbi` 或 `hbi_` 开头的中间品ID追溯实际生产池。将可核验绿氢HBI与其他HBI拆池，避免混合池的来源歧义。签发同时受钢材排放门槛和前体属性兼容规则约束。

## 数据接口的变更

- 投资以 `(option_id, year)` 为键，所有年份均保留，重复批次报错。
- 25表的年度参数以 `(scenario, parameter, location, year)` 为键；无年份行是明确的常数路径。查当年参数优先匹配年度值，不把后一年覆盖为全期常数。
- 同一输入文件有多个 `demand_case` 时必须明确选择；模型仅将同一钢种配方交付计入对应钢种需求。
- 初始资产 `retirement_year` 是首个不再可用的年份；未给该值时取 `start_year + design_life_years`。同池不同资产利用率分别加权，不再取所有资产的最低利用率。
- 已承诺、尚未投运的固定能力可作为 `04_assets.csv` 的 `committed` 或 `construction` 行，`start_year`填写有证据的投运年。此接口只处理能力路径；未支付的共同承诺支出尚需单独完善，不能称完整管线资本账。
- 22表绝对权属需要 `plant_id`到配方的明确映射，或补充经核验的 `recipe_id`。不支持根据国家名猜测权属。上限与ρ约束并用取较紧约束，不进行第二次数量扣减。
- 24表共享年度建设限制目前支持 `limit_kind=new_build`，`upper_value`按原始t工序产品/年能力单位；配方占用系数和池单位需一致。改造/基础设施类型尚未接入。
- 新建在末年之后才投运时，当前保守规则不给在建工程残值；已经投运的设备按剩余服务寿命计值。可用 `--terminal-rule zero`作共同零残值敏感性。
- 输入重复键、缺资源、缺可转让比例、无效价格参考、负MRV费用等均报错或返回失败，不用零值补齐。

- 表16的原料市场弧使用 `market_trade=1`、`trade_allowed=0/1`，商品ID采用 `scrap`、`iron_ore`、`ore` 或 `pellets` 及其质量分组前缀。供方表13对应资源以吨计，配方从目的地资源池取用。发运量占用走廊限额，按 `loss_fraction` 折算到货量。
- 受管制的原料需在表18按实际对应商品录入 `variable_kind=f` 和 `variable_key=起点|终点|资源ID`。系数将发运吨换成实际入境吨。出口方默认取生产地或发运地，也可在表17按入境责任类别显式填写 `exporter_region`。
- 配套 `certificate_strict_benchmarks.csv` 包含 `grade_id,year,benchmark_tco2e_per_tcs,source_id`。外部严格档必须有可追溯来源并使用与表19责任参考账一致的边界。真实输入中的来源登记需要注明样本范围、产量加权法和基准年份。

## 运行

在 `code/` 目录执行。Python需为3.11及以上，并安装仓库根目录的 `requirements.txt`。

```sh
python -m greenbook.run_model --synthetic-demo --output results/fixed_recipe_certificate_demo
python -m greenbook.run_model --synthetic-demo --include-hbi-trade --output results/fixed_recipe_certificate_hbi
python -m unittest discover -s greenbook/tests -v
```

校准实例可将严格档表放入实例目录，也可通过参数指定。

```sh
python -m greenbook.run_model --instance PATH_TO_INPUT --demand-case MAIN --strict-benchmarks PATH_TO_CSV --output results/observed_case_run
```

严格档缺失会报错，合成算例的严格档数值仅用于测试。历史结果可用 `--legacy-policy` 或原 `--kappa-grid` 参数复算。

### 离网和并网电价

采用 [Devlin 等（2023）](https://www.nature.com/articles/s41467-023-38123-2) 的两种供电口径。离网在给定氢交付和可选钢厂直接用电时，联合配置风电、光伏、电解槽、电池和氢储存。离网平准化电力成本为风光和电池年成本除以终端实际用电量，分母包括电解及直接用电。零收益弃电和储能损失通过成本反映。并网基准按外部工业电价逐时乘购电量，恒定氢交付与直接负荷只靠电网供电，电解槽年成本另计。论文中的历史工业电价和未来成本结果用于参考，配置应填研究价格年下的节点数据。

给年度模型加 `--energy-supply PATH_TO_JSON`。JSON 是项目块列表，每项固定一个节点、年份、氢资源ID、块ID和供电方式。下列是并网制氢的合成配置，24小时短周期仅用于测试输入：

```json
[
  {
    "node_id": "X", "year": 2027, "resource_id": "h2_grid", "block_id": "G1",
    "source_id": "ILLUSTRATIVE_ONLY", "power_mode": "grid",
    "input": {
      "hours": [12, 12], "delivery_kg_per_hour": 2000,
      "grid_price_EUR_MWh": 60, "grid_emissions_kgCO2_MWh": 400, "grid_limit_MW": 100,
      "electrolysis_kg_MWh": 20, "annual_cost": {"electrolyser": 50000},
      "capacity_limit": {"electrolyser": 100}
    }
  }
]
```

- `hours` 是连续时段的小时数，其余逐时字段接受等长数组或标量。真实计算用完整的8760小时时序。短周期按年度等比例重复，每个周期库存首尾闭合。
- 离网填 `power_mode: islanded`、`wind_cf`、`solar_cf` 及可用风光的 `wind_emissions_kgCO2_MWh`、`solar_emissions_kgCO2_MWh`，并在 `annual_cost` 和 `capacity_limit` 中填 `wind`、`solar`、`electrolyser`、`battery_power`、`battery_energy`、`hydrogen_store` 六项。成本为统一价格年的年化设备成本和固定运维，按相应的 MW、MWh 或 kgH₂计。设备更换可折入年成本。并网的电价、排放因子及接入功率上限由输入提供。
- 表12配方须使用对应资源ID。并网与离网氢分别用不同ID，同一组资源供所有政策情景选择。项目可替换表13同键的唯一供给块，或新增专用资源ID。工程交付量为年度上限，平均成本作为该块内的固定单位成本。限额输入应已扣除其他用能承诺。诊断文件记录设备规模、购电量、氢价、电价和排放，运行清单记录JSON的哈希。
- 若同时计入DRI/EAF等工序直接用电，填 `input.direct_load_MW` 与项目的 `electricity_resource_id`，在表12填写 MWh/吨产品的电力系数。共同供电成本与排放按实际用电量分摊，氢价只承担制氢份额。已列入供给块的电费、电解设备成本及系统排放从其他对应项目中剔除。直接用电适用的GSC资格排放仍按表19填报。
- 离网的 `renewable_power_evidence_id` 要填经核验的证据才能进入GSC资格判断。并网工业基准没有可追溯绿电采购证明，仍可供物理生产。钢材排放门槛和CBAM责任按各自既定口径计算。

### Devlin 工序校准与候选节点

在 `code/` 下运行以下合成示例。配置可与 `--energy-supply` 同时使用，先生成工序投入，再求解工程能源块。

```sh
python -m greenbook.run_model --synthetic-demo --legacy-policy --devlin-inputs greenbook/examples/devlin_synthetic.json --output results/devlin_structure
```

- `pellet_blocks` 按节点、质量资源ID、独立供给块和年份，填干基球团的铁质量份额和脉石质量份额。上限取矿石对应的球团产量、球团厂产量和交付量的最小值，再减去其他已承诺用途。不同块的共同产能须先分配。可填 `private_price_per_t`，或输入矿石品位、论文价格年美元至本模型欧元的转换系数及到厂运费，利用论文式(1)生成情景价格。
- `recipes` 改写表11已有配方对应的表12投入和DRI占用，保留其他共享池。路线可选 `hot_dri`、`cold_dri`、`hbi_production` 和 `hbi_eaf`。0%、25%及50%废钢配方可用S10的EAF电耗参考值；为每个选项准备独立配方ID、适用的表17–19记录及证书权属，年度LP在同一供给约束下选择。输入钢中Fe、炉料Fe和各铁回收率，按铁平衡计算炉料量。选填石灰、合金、电极资源，或在工序费用中列支。
- `electricity_blocks` 可直接填表13所用的年度工序电量、价格及系统排放因子。也可通过 `--energy-supply` 对应的电力ID生成。离网工程块可设 `renewable_land_km2`，按光伏45、风电8 MW/km²的参考值计算共同用地，并允许覆盖系数。离网或并网电解块可设 `hydrogen_water_limit_m3_per_year`，用水参考强度为12 L/kgH₂，可按节点覆盖；只包括电解相关用水。共享地和水须在输入端向工程块分配额度。
- 跨境HBI按发运吨计运输、排放及走廊容量，按入境吨映射表18，按实际入炉吨计算加热和原料用量。多个来源池共用同一条走廊时，填写相同的 `shared_corridor_id` 与共同上限。再加热电耗必须显式输入，因S10表列单位存在疑点；示例0.15 MWh/t仅用于测试。表17的合规强度、表19的全套排放分量按配方独立维护，`emissions_source_id` 需匹配表19。显式购电、球团或碳辅料已进入资源账的费用及系统排放从其他对应项目剔除。合成示例仅验证结构。
- 候选节点可用 Devlin 的 Source Data 工作簿 `Fig 5` 按论文LCOS排序，需要 `openpyxl`。也可输入已完成项目工程优化的训练CSV和待筛选CSV，需要 `scikit-learn`；按国家分组嵌套验证，分别估算供电成本和剔除矿石与人工的钢成本，后两项再按节点加回。特征列以 `greenbook.analysis.devlin_screen.FEATURES` 为准，含安装年份以及风光小时/月度容量因子的均值、中位数和变异系数。训练CSV需有 `country`、`energy_EUR_per_MWh`、`lcos_ex_ore_labour_EUR_per_tcs`，候选CSV需有 `site_id`、`country`、`ore_EUR_per_tcs`、`labour_EUR_per_tcs`。筛选结果用于安排工程校准，供给、签发资格仍以核验后的节点输入为准。

```sh
python -m greenbook.analysis.devlin_screen --published-xlsx PATH_TO_Source_Data.xlsx --output results/devlin_sites.csv
python -m greenbook.analysis.devlin_screen --training training.csv --candidates candidates.csv --output results/devlin_sites_fitted.csv
```

## 结果与结构核验

- `certificate_benchmarks.csv` 记录冻结的产品和年度基准及严格档来源。
- `certificate_issuance.csv` 记录配方废钢比、资格强度、门槛、责任参考强度、每吨钢认可量、兼容交付、比例上限以及实际签发核销量。比例上限仍受绝对权属约束。 `certificates_and_clearing.csv` 按年度对账。
- `exporter_payments.csv` 记录各出口方的净责任、CBAM及保留费用、证书采购支出。 `resource_trade.csv` 记录各原料弧的发运量和到货量。
- `policy_comparison.csv` 报告各档累计系统排放、与C的差额及欧盟自产和进口。 `summary.md` 另报全期折现成本。
- `solver_status.csv`、`independent_audit.json` 和 `run_manifest.json` 记录求解、独立物理/现金流核算、输入来源及版本。 `synthetic_input/` 保存该次生成的全部合成输入。
- 历史结构输出留在 `code/results/dynamic_structure_2026_2040/`，对应上一版签发与核销规则。

## 后续建模工作的明确缺口

1. 2026初始资产、真实项目管线、工艺配方及2030—2040资源路径的采集、配平和验证。已有2026年9月GEM快照仍不能自动成为1月1日三路线基线。
2. 质量与商品转换验证器、实际矿源/回收供给和跨境运力校准、未覆盖路线的供需及排放闭合。
3. 带源批次的改造、内生提前退出、实际生产到新增低碳还原批次的资格追踪。
4. 清洁能源与制氢原型扩展为有明确年景、供水、压缩、上游排放及交付可靠性的工程供给块。
5. 最优面排放与价格可识别区间、真实基年留出验证及成本/权属/终端敏感性。

详细方案中允许首轮关闭改造与提前退出，本轮按该阶段边界实现自然寿命、新建及闲置。
