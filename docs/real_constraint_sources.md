# IPF 目标约束来源

生成的目标文件：

- `data/targets/demographic_targets_2025.json`

抽样框：

- 18-80 岁成年人。
- 当前生成器支持 26 国：United States、Canada、Mexico、Brazil、Colombia、United Kingdom、Germany、France、Poland、China、Japan、South Korea、Indonesia、Philippines、Vietnam、India、Pakistan、Bangladesh、Egypt、Turkey、Iran、Nigeria、Ethiopia、South Africa、Kenya、Australia。

## 约束列表

| 约束 | 来源 | 处理方式 |
| --- | --- | --- |
| `country_population` | UN DESA World Population Prospects 2024 | 使用 2025 年中位方案人口，限制为 18-80 岁成年人，并在 26 个支持国家之间归一化。 |
| `current_country_x_current_city_size` | UN DESA World Urbanization Prospects 2025 File 21 | 26 国的 8,422 个 50,000+ DEGURBA 城市聚合为七个规模档；具体城市在档内按 2025 人口抽样。 |
| `age_x_sex` | UN DESA World Population Prospects 2024 | 使用 2025 年中位方案的 5 岁年龄组 x 性别人口，并聚合到生成器使用的年龄段。 |
| `current_country_x_age_group_x_sex` | UN DESA World Population Prospects 2024 | 保留 26 国各自的 2025 年年龄×性别结构，不再只使用全球年龄×性别边际。 |
| `country_x_education` | World Bank WDI 教育成就指标 | 使用各国 25 岁以上人口的最新非空观测值；累积指标会差分成生成器的教育层级。 |
| `country_x_religion` | Pew Research Center 宗教构成表 | 使用 2020 年国家宗教构成。Pew 属于权威研究来源，但不是官方统计机构来源；这是当前唯一仍非官方的 IPF 输入。 |
| `current_country_x_citizenship_country` | UN DESA International Migrant Stock 2020 | 使用官方目的地—来源地矩阵。目的地数据类型含 `C` 时直接表示国籍；含 `B` 时是出生国来源的官方代理。在 26 国国籍集合内条件化。 |
| `current_country_x_birth_country` | UN DESA International Migrant Stock 2020 | 使用同一官方矩阵。目的地数据类型含 `B` 时直接表示出生国；含 `C` 时是国籍来源的官方代理。在 26 国出生国集合内条件化。 |
| `current_country_x_sex_x_birth_migration_status` | UN DESA International Migrant Stock 2020 | 读取 destination×origin×sex，派生出生地与现居国相同/不同的 `local`/`foreign` 状态。 |
| `current_country_x_sex_x_citizenship_migration_status` | UN DESA International Migrant Stock 2020 | 使用同一性别分解矩阵派生国籍 local/foreign；继续保留各目的地 `B`/`C` mixed-basis caveat。 |
| `current_country_x_ethnicity` | UNData table 26、各国官方人口普查、UN DESA IMS 2020 | 改为按当前居住国约束，因为官方人口群体统计描述的是居民而非国籍人口。兼容的普查类别直接映射到固定 taxonomy；缺少兼容族裔表的国家用官方移民来源地区做宽类别映射。 |
| `country_x_income_level` | World Bank Poverty and Inequality Platform；OECD《Under Pressure》相对收入分类 | 优先使用各国最新非插值全国记录，以该国 2021 PPP 日人均收入/消费中位数 `M` 划分：`<0.75M`、`0.75M–1.00M`、`1.00M–1.50M`、`1.50M–2.00M`、`>=2.00M`。 |
| `age_x_sex_x_relationship_status` | UNData table 23、UN WPP 2024 | 使用支持国家中最近可用的婚姻状态×年龄×性别官方观测，由 WPP 2025 年 26 国年龄性别人口加权。`In consensual union` 映射为 `in relationship`，分居并入 `divorced`。 |
| `current_country_x_age_group_x_sex_x_relationship_status` | UNData table 23、UN WPP 2024 | 保留逐国年龄×性别×关系状态表，而不是仅保留跨国加权后的全球表。 |
| `current_country_x_relationship_status_x_employment_status` | ILOSTAT observed 年龄×性别×婚姻状态就业人口比、UNData table 23 | 在每个国家年龄性别单元内校准婚姻状态就业率，再聚合为国家×关系×就业；严格保留关系与就业边际。 |
| `current_country_x_sex_x_occupation` | ILOSTAT 2025 modelled estimates、UN WPP 2024、ISCO-08 分类 | ILOSTAT 的就业人口比给出 `None`；IPF 校准 8 个官方可比父桶，父桶内从 115 个简短、具体且保持父桶映射的岗位名称中采样。细项不是独立统计约束。 |
| `current_country_x_age_group_x_sex_x_ilo_education_group` | ILOSTAT observed working-age population、WDI、UN WPP 2024 | 年龄×性别×四档教育观测结构 raking 到国家年龄性别和教育边际。 |
| `current_country_x_age_group_x_sex_x_employment_status` | ILOSTAT observed 年龄就业曲线、2025 modelled employment margin | 10 岁年龄档就业曲线经 logit intercept 校准到国家×性别就业边际。 |
| `current_country_x_sex_x_ilo_education_group_x_employment_status` | ILOSTAT observed 教育就业率 | 教育就业关系校准到一致的国家×性别教育和就业边际。 |
| `current_country_x_ilo_education_group_x_occupation_group` | ILOSTAT observed occupation×education | 观测关系 raking 到一致的国家教育和职业边际。 |
| `current_country_x_age_stage_x_sex_x_occupation_group` | ILOSTAT observed youth/adult×sex×occupation | 青年/成人职业关系 raking 到年龄阶段就业与国家×性别职业边际；`None` 表示未就业。 |
| `current_country_x_welfare_education_group_x_income_level` | World Bank Global distribution of welfare 2022、WDI、PIP | 四档教育的 `$2.15`/`$6.85` 条件 headcount 定义福利秩区间，区间内最大熵映射到五档相对中位数收入；教育和收入边际保持精确。 |
| `current_country_x_age_stage_x_income_level` | World Bank Global distribution of welfare 2022、UN WPP 2024、PIP | youth/adult 条件福利关系用同一方法映射到五档收入；年龄阶段和收入边际保持精确。 |
| `current_country_x_age_group_x_sex_x_physical_condition` | IHME GBD 2023 Results Tool prevalence rate、UN WPP 2024 | 使用 26 国、男女、14 个 GBD 五岁年龄组和 22 个身体疾病 cause；按 WPP 人口权重聚合到生成器年龄段，并保留国家×年龄×性别差异。 |
| `current_country_x_age_group_x_sex_x_mental_condition` | IHME GBD 2023 Results Tool prevalence rate、UN WPP 2024 | 使用同一国家×年龄×性别切片覆盖 16 个精神/物质使用标签；抑郁障碍和进食障碍由对应 GBD 叶子 cause 合并。 |

## 官方数据转换规则

当前静态目标中已经没有 `prior_fallback`。但是“由官方输入派生”不等于“官方直接发布了这个联合表”：

- 国籍/出生国约束逐国保留 IMS 的 `B`（出生国）或 `C`（国籍）数据基础；用于另一个字段时明确标成代理。
- 国籍和出生国目前表示 26 个支持国家，因此目标是“本国加 26 国中其他国家”的条件分布，不是完整全球来源国分布。
- 族裔 taxonomy 是项目自己的宽类别，映射规则及原始分布记录在 `data/sources/official_ipf/derived_inputs_2025.json`。
- 收入五档是相对于各国 2021 PPP 日福利中位数的区间，不是国家内五分位。
- 移民、收入和部分族裔输入没有统一的 18–80 岁切片，当前假设其条件分布可用于成年抽样框；年龄边际仍由 WPP 18–80 岁数据控制。
- PIP 的福利概念逐国可能是收入或消费，具体年份和口径记录在派生输入审计文件中。
- 世界银行教育/年龄条件数据并未直接发布本项目的五档相对收入表；最大熵只发生在两条已观测贫困线形成的福利秩区间内部。教育×福利缺失组和年龄×福利缺失国使用同一数据集的区域关系，并逐国标记。如果该文件内某一条件组的人口份额与正式 WDI/WPP 边际相差超过 10 倍，则该国整项条件关系改用同区域关系，避免把覆盖极窄的分组率外推到大量人口。
- `age_stage=youth` 在 World Bank 和 ILOSTAT 原表中是 15–24 岁，在生成器中是 18–24 岁；`adult` 的原表上界开放，而生成器截止到 80 岁。
- 只有官方表明确报告 `In consensual union` 时才产生 `in relationship` 质量；没有报告不能解释成该国现实中不存在同居。
- ILOSTAT 的就业人口比年龄口径为 15 岁以上，当前用于 WPP 的 18–80 岁抽样框。可稳定跨国比较并进入 IPF 的仍是 ISCO-08 父级；最终输出的具体岗位名是父级内的语义细化，不应解释成拥有独立的国家—性别统计比例。
- `physical_condition` 和 `mental_condition` 是单标签字段，而 GBD 原始 prevalence 允许共病。每个国家×年龄×性别单元先用 `1-product(1-p_i)` 近似至少具有一个所选状况的比例，再按各 cause prevalence 的相对权重分配一个代表标签；剩余质量记为 `None`。这是项目为单标签采样所做的显式转换，不代表 GBD 声称疾病互斥。
- GBD 的 15–19 岁组仅以 `2/5` 权重进入生成器的 18–24 岁组；65–79 岁完整计入 65–80 岁组，80–84 岁组以 `1/5` 权重计入。所有聚合权重来自同国同年龄同别的 WPP 2025 人口。

## 当前静态目标状态

内置 JSON 已移除已知有问题的占位约束：

- `citizenship_country_population`
- `birth_country_population`
- `ethnicity`
- `income_level`
- `relationship_status`

这些约束已被结构化的官方/官方派生约束替换，同时仍保证每个 skeleton 字段都被 IPF 覆盖。宗教分布仍为 Pew 权威非官方来源，因为 26 国中包括美国、中国、日本、尼日利亚等没有可比的官方人口宗教统计。

## Skeleton 覆盖

每个 skeleton 字段至少参与一个 IPF 约束：

| Skeleton 字段 | IPF bucket 字段 | 约束 |
| --- | --- | --- |
| `age` | `age_group`, `age_stage` | `age_x_sex`, `current_country_x_age_group_x_sex`, `current_country_x_age_group_x_sex_x_relationship_status`, `current_country_x_age_stage_x_income_level`, `current_country_x_age_stage_x_sex_x_occupation_group` |
| `sex` | `sex` | `age_x_sex`, `age_x_sex_x_relationship_status`, `current_country_x_sex_x_occupation` 及两条国家×年龄×性别健康约束 |
| `ethnicity` | `ethnicity` | `current_country_x_ethnicity` |
| `citizenship` | `citizenship_country` | `current_country_x_citizenship_country` |
| `current_location` | `current_country` | 所有 `current_country_x_*` 约束及 `country_population` |
| `birth_location` | `birth_country`, `birth_migration_status` | `current_country_x_birth_country`, `current_country_x_sex_x_birth_migration_status` |
| `education_level` | `education_level`, `ilo_education_group`, `welfare_education_group` | `country_x_education` 及教育×年龄/就业/职业/收入联合约束 |
| `income_level` | `income_level` | `country_x_income_level`, `current_country_x_welfare_education_group_x_income_level`, `current_country_x_age_stage_x_income_level` |
| `relationship_status` | `relationship_status` | 全球/逐国年龄性别关系表及 `current_country_x_relationship_status_x_employment_status` |
| `religious_belief` | `religious_belief` | `country_x_religion` |
| `occupation` | `occupation_group`, `employment_status` | 国家×性别职业、年龄阶段×职业、教育×职业及教育/关系/年龄×就业约束 |
| `physical_condition` | `physical_condition` | `current_country_x_age_group_x_sex_x_physical_condition` |
| `mental_condition` | `mental_condition` | `current_country_x_age_group_x_sex_x_mental_condition` |

## 数据链接

- UN WPP 下载页：https://population.un.org/wpp/downloads
- 使用的 UN WPP CSV：`WPP2024_PopulationByAge5GroupSex_Medium.csv.gz`
- World Bank API 示例：https://api.worldbank.org/v2/country/USA/indicator/SE.PRM.CUAT.ZS?format=json
- 使用的 World Bank 教育指标：`SE.PRM.CUAT.ZS`, `SE.SEC.CUAT.LO.ZS`, `SE.SEC.CUAT.UP.ZS`, `SE.SEC.CUAT.PO.ZS`, `SE.TER.CUAT.BA.ZS`, `SE.TER.CUAT.MS.ZS`, `SE.TER.CUAT.DO.ZS`
- World Bank PIP API：https://api.worldbank.org/pip/v1
- World Bank 2021 PPP 贫困线说明：https://www.worldbank.org/en/news/factsheet/2025/06/05/june-2025-update-to-global-poverty-lines
- Pew 宗教表：https://www.pewresearch.org/religion/feature/religious-composition-by-country-2010-2020/
- UNData 婚姻状态：https://data.un.org/Data.aspx?d=POP&f=tableCode%3A23
- UNData 族裔：https://data.un.org/Data.aspx?d=POP&f=tableCode%3A26
- UN DESA International Migrant Stock：https://www.un.org/development/desa/pd/content/international-migrant-stock
- Statistics Canada 2021 population group：https://www12.statcan.gc.ca/census-recensement/2021/dp-pd/prof/details/page.cfm?DGUIDlist=2021A000011124
- INEGI 2020 人口普查：https://www.inegi.org.mx/contenidos/productos/prod_serv/contenidos/espanol/bvinegi/productos/nueva_estruc/702825198060.pdf
- ILOSTAT bulk 数据说明：https://rplumber.ilo.org/data/bulk/
- ILOSTAT 就业人口职业分布：https://rplumber.ilo.org/files/indicator/EMP_2EMP_SEX_OCU_NB_A.rds
- ILOSTAT 就业人口比：https://rplumber.ilo.org/files/indicator/EMP_2WAP_SEX_AGE_RT_A.rds
- ILOSTAT 年龄×教育劳动年龄人口：https://rplumber.ilo.org/files/indicator/POP_XWAP_SEX_AGE_EDU_NB_A.rds
- ILOSTAT 年龄×教育就业人数：https://rplumber.ilo.org/files/indicator/EMP_TEMP_SEX_AGE_EDU_NB_A.rds
- ILOSTAT 职业×教育就业人数：https://rplumber.ilo.org/files/indicator/EMP_TEMP_SEX_OCU_EDU_NB_A.rds
- ILOSTAT 年龄阶段×职业就业人数：https://rplumber.ilo.org/files/indicator/EMP_TEMP_SEX_AGE_OCU_NB_A.rds
- ILOSTAT 年龄×婚姻状态就业人口比：https://rplumber.ilo.org/files/indicator/EMP_DWAP_SEX_AGE_MTS_RT_A.rds
- World Bank Global distribution of welfare 2022：https://datacatalog.worldbank.org/search/dataset/0066601/global-distribution-of-welfare-2022
- IHME GBD Results Tool：https://vizhub.healthdata.org/gbd-results/
- 本项目使用的导出切片：GBD 2023、Prevalence、Rate、Both sexes 分拆为 Male/Female、2023 年、26 国、14 个年龄组、40 个 cause。构建脚本会严格验证应有的 29,120 行，并把文件 SHA-256、维度覆盖和 cause 映射写入审计文件。

## 可复现构建

安装依赖后运行：

```bash
python scripts/rebuild_official_ipf_targets.py \
  --ihme-gbd .cache/official_ipf/ihme_gbd_2023.csv
```

脚本会更新目标 JSON、派生输入审计文件以及当前约束值文档。IHME 导出文件和其他原始下载保存在 `.cache/official_ipf/`，不进入版本控制；未提供完整且维度匹配的 IHME 文件时构建会失败，而不会回退到旧的全球先验。

## 教育层级映射

World Bank 的累积教育成就指标映射如下：

- `none = 100 - at_least_primary`
- `primary = at_least_primary - at_least_lower_secondary`
- `lower secondary = at_least_lower_secondary - at_least_upper_secondary`
- `upper secondary = at_least_upper_secondary - at_least_post_secondary`
- `vocational = at_least_post_secondary - at_least_bachelor`
- `bachelor = at_least_bachelor - at_least_master`
- `master = at_least_master - doctorate`
- `doctorate = doctorate`

已知回退：

- Brazil 在拉取到的 World Bank 序列中没有最新可用的 `post_secondary` 值，因此设置为等于 `bachelor`。
- Japan 没有最新可用的 `doctorate` 值，因此设置为 `0.0`。
