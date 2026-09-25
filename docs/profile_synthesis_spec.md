# Profile 合成字段与 IPF 约束设计

## 1. 程序随机组合字段及候选项（seed）

程序生成 13 个结构化 seed 字段并机械去重。候选生成负责提供覆盖面，IPF 负责校准联合分布；现实一致性统一由 LLM 审核，不因罕见而拒绝。

### 1.1 age

候选范围：18—80 岁整数。

IPF 年龄分组：18—24、25—34、35—44、45—54、55—64、65—80。

### 1.2 sex

候选项：male、female。

### 1.3 ethnicity

候选项：East Asian、SE Asian、South Asian、MENA、Black、White、Latino、Indigenous、Pacific Islander、Mixed。

这些类别是项目统一 taxonomy，不等同于任何单一国家人口普查的原始分类。各国官方类别需要映射到该 taxonomy。

### 1.4 citizenship

当前使用以下 26 国候选集合：

| 大区 | 国家 |
| --- | --- |
| Northern America | United States、Canada |
| Latin America | Mexico、Brazil、Colombia |
| Europe | United Kingdom、Germany、France、Poland |
| East Asia | China、Japan、South Korea |
| Southeast Asia | Indonesia、Philippines、Vietnam |
| South Asia | India、Pakistan、Bangladesh |
| MENA | Egypt、Turkey、Iran |
| Sub-Saharan Africa | Nigeria、Ethiopia、South Africa、Kenya |
| Oceania | Australia |

这 26 国全部通过 UN WPP 2024、UN WUP 2025、UN DESA IMS 2020、ILOSTAT 2025、World Bank 教育指标、World Bank PIP 和 Pew 2020 宗教构成的约束覆盖检查。每个包含 current_country 的 IPF 约束都必须对全部 26 国具有正 support。

### 1.5 current_location

值由“城市，国家”组成。城市候选来自 UN DESA World Urbanization Prospects 2025 File 21，纳入 2025 年人口不少于 50,000 的 DEGURBA city。

在 26 国集合下共有 8,422 个 WUP 2025 城市记录：

| 国家 | 城市数 | 人口最多的五个城市 |
| --- | ---: | --- |
| United States | 348 | New York City、Los Angeles、Houston、Chicago、Washington, D.C. |
| Canada | 56 | Toronto、Montreal、Vancouver、Calgary、Edmonton |
| Mexico | 186 | Ciudad de México、Guadalajara、Monterrey、Puebla、Tijuana |
| Brazil | 356 | São Paulo、Rio de Janeiro、Belo Horizonte、Recife、Fortaleza |
| Colombia | 81 | Bogotá、Medellín、Cali、Barranquilla、Cartagena |
| United Kingdom | 150 | London、Birmingham、Manchester、Liverpool、Leeds |
| Germany | 100 | Berlin、Essen、Munich、Hamburg、Cologne |
| France | 71 | Paris、Lyon、Lille、Marseille、Toulouse |
| Poland | 60 | Warszawa、Katowice、Krakow、Lodz、Wrocław |
| China | 2,082 | Shanghai、Guangzhou、Beijing、Shenzhen、Suzhou |
| Japan | 137 | Tōkyō、Osaka、Nagoya、Fukuoka、Sapporo |
| South Korea | 49 | Seoul、Busan、Daegu、Gwangju、Daejeon |
| Indonesia | 389 | Jakarta、Bandung、Surabaya、Medan、Tasikmalaya |
| Philippines | 114 | Manila、Cebu City、Davao City、Angeles、Dagupan |
| Vietnam | 137 | Ho Chi Minh City、Hà Nội、Cần Thơ、Đà Nẵng、Hải Phòng |
| India | 2,054 | New Delhi、Kolkata、Mumbai、Bengaluru、Chennai |
| Pakistan | 323 | Karachi、Lahore、Faisalabad、Islāmābād、Peshawar |
| Bangladesh | 233 | Dhaka、Chattogram、Savar、Brahmanbaria、Kishoreganj |
| Egypt | 219 | Cairo、Alexandria、Luxor、Banha、El Mansura |
| Turkey | 177 | Istanbul、Ankara、Izmir、Bursa、Gaziantep |
| Iran | 210 | Tehran、Mashhad、Karaj、Isfahan、Ahwaz |
| Nigeria | 457 | Lagos、Onitsha、Kano、Owerri、Ibadan |
| Ethiopia | 259 | Addis Ababa、Adama、Bahir Dar、Dire Dawa、Shashamane |
| South Africa | 95 | Johannesburg、Cape Town、Durban、Pretoria、Klipgat |
| Kenya | 50 | Nairobi、Mombasa、Nakuru、Kisumu、Eldoret |
| Australia | 29 | Sydney、Melbourne、Perth、Brisbane、Adelaide |

城市候选保持完整列表，但不直接对数千个城市类别运行精确 country × city IPF。城市人口规模分为 50k—100k、100k—250k、250k—500k、500k—1m、1m—5m、5m—10m、10m+ 七档；IPF 校准 country × city size class，档内再按 WUP 2025 城市人口加权抽取具体城市。

### 1.6 birth_location

候选范围与 current_location 使用相同的 WUP 2025 城市目录。

IPF 只约束 birth_country，不把 WUP 当前城市人口当作出生城市分布。具体出生城市在目标出生国内按候选机制抽取。

### 1.7 education_level

候选项：none、primary、lower secondary、upper secondary、vocational、bachelor、master、doctorate。

### 1.8 income_level

候选项：low、lower-middle、middle、upper-middle、high。

五档表示相对于所在国福利中位数的区间，不是跨国统一的绝对收入金额。

### 1.9 relationship_status

候选项：single、in relationship、married、divorced、widowed。

### 1.10 religious_belief

候选项：Unaffiliated、Christian、Muslim、Hindu、Buddhist、Jewish、Folk。

### 1.11 occupation

ILOSTAT 实际稳定提供 8 个可比职业父桶和 None：

- None
- managers
- professionals
- technicians and associate professionals
- clerical support workers
- service and sales workers
- craft and related trades workers
- plant and machine operators and assemblers
- elementary and skilled agricultural workers

None 表示未就业；其质量由 ILOSTAT 就业人口比推导，已就业人口再分配到职业大类。

最终 occupation 字段不直接输出这些宽泛父桶，而是在对应父桶内采样简短、具体的岗位名称。当前共 116 个可见候选，包括 None 和 115 个就业岗位。例如 professionals 会展开为 civil engineer、physician、registered nurse、primary school teacher、accountant、software developer、lawyer 等具体角色。细项保留与 ISCO-08 父桶的确定映射，但不冒充拥有独立跨国统计权重；IPF 仍只校准官方父桶。

### 1.12 physical_condition

`physical_condition` 直接作为 IPF 字段，不再使用 `physical_condition_group`，也不再把一个有统计权重的父类均匀拆成无独立权重的严重度、部位或并发症子型。

当前包含 None 和 22 个在 IHME GBD 2023 导出中拥有国家×年龄×性别患病率的疾病跨度：

- 神经及脑血管：migraine、tension-type headache、idiopathic epilepsy、Parkinson's disease、Alzheimer's disease and other dementias、stroke
- 感觉系统：age-related and other hearing loss
- 肌肉骨骼：osteoarthritis、rheumatoid arthritis、low back pain
- 呼吸系统：asthma、chronic obstructive pulmonary disease
- 代谢和肾脏：type 2 diabetes、chronic kidney disease
- 心血管：atrial fibrillation and flutter
- 消化系统：gastritis and duodenitis、peptic ulcer disease
- 皮肤系统：dermatitis、urticaria、psoriasis、alopecia areata
- 感染性疾病：HIV/AIDS

GBD 患病率允许同一个人同时患有多个疾病，而当前字段只能保存一个代表标签。因此不能把 22 个患病率直接相加并声称它们互斥。每个国家×年龄×性别单元先以 `1 - product(1 - p_i)` 明确近似“至少有一个所选疾病”的质量，再按各疾病患病率的相对比例分配单标签；None 是该所选疾病集合之外的剩余质量，不表示个体没有任何其他身体疾病。

### 1.13 mental_condition

`mental_condition` 直接作为 IPF 字段，候选项为 None 和 16 个有统计支撑的 span：

- GBD 2023 的 10 个 mental-disorder span：schizophrenia、depressive disorders、bipolar disorder、anxiety disorders、eating disorders、autism spectrum disorders、ADHD、conduct disorder、idiopathic developmental intellectual disability、other mental disorders。
- GBD 单独建模的 6 个 substance-use span：alcohol use disorders、opioid use disorders、amphetamine use disorders、cocaine use disorders、cannabis use disorders、other drug use disorders。

原有 28 个候选中的 bipolar I/II、具体焦虑障碍、ADHD presentation、智力障碍严重程度等没有各自的同口径权重，仍不恢复。抑郁障碍和进食障碍由对应 GBD 叶子 cause 合并；其他标签使用直接 cause。每个国家×年龄×性别单元仍以 `1 - product(1 - p_i)` 处理标签间重叠，再转换为一个代表标签。

## 2. LLM 补全字段

LLM 保持 13 个 seed 字段不变，只补全 name、phone_number、email、government_id。

### 2.1 name

使用普通、现实的姓名形式。姓名应综合 citizenship、birth_location、current_location、ethnicity 和迁移背景，但任何单一字段都不能机械决定姓名。不得因为 religion、income、health、occupation 或 relationship status 选择姓名，也应避免公众人物和著名虚构角色。

### 2.2 phone_number

使用真实国家电话号码格式，包括合理的国际国家码、国内前缀、位数和分组方式。不再强制使用 555、000 或其他显式虚构号段。

号码国家通常跟随 current_location；对于迁移者，保留 citizenship 国号码也合理。程序允许号码国家码与 current_location 或 citizenship 中任一国家一致，但不允许把一个国家的国际区号和另一个国家的国内号码格式拼接在一起。

### 2.3 email

域名必须使用 example.com、example.org 或 example.net，避免邮件实际投递给第三方。除此之外不预设本地部分的生成模板；它可以与姓名有关，也可以完全不包含姓名。跨样本重复由程序检测并触发重采样，而不是通过强制加入姓名、candidate_id 或电话号码尾号来保证唯一性。

### 2.4 government_id

使用 citizenship 国家常见的个人政府标识格式，不再插入 SYN、TEST 或字母国家标记。若该国格式编码了出生年份、性别、姓名组成或地区信息，应与 seed 中可获得的信息一致。

当前 schema 没有 government_id_type，因此补全时为每个国家选择一种稳定、常见的个人标识格式，并在同一批生成中保持一致。

## 3. 总体 Pipeline

1. 从候选集合随机组合生成 13 个 seed 字段，混合均匀探索与目标引导提议分布。
2. 程序只做 seed 去重，不做现实一致性过滤。LLM 统一审核是否至少存在一种现实人生经历能让全部字段同时成立；迁移、归化、改宗、低概率疾病或非典型教育—收入组合不能仅因罕见而被拒绝。当前实现没有单独的学历—年龄 hard rule，审核 prompt 也没有写死 master 或 doctorate 的年龄阈值，这类组合按通用的跨字段逻辑一致性规则交给 LLM 判断。
3. LLM 基于通过审核的 seed 补全四个 PII 字段，并保持 seed 不变。Prompt 只要求 PII 与 seed 在现实世界中逻辑一致、字段格式正确，以及邮箱使用保留域名，不指定姓名、邮箱、电话或 government_id 的固定生成模板。
4. 程序分别校验 name、email、phone_number 和 government_id，并检测 email、phone_number 和 government_id 的全局重复。通过程序校验的 government_id 还会进入独立的 LLM 逻辑一致性审核；审核只判断证件类型及其中可解释的出生、性别、姓名、地区信息能否与完整 profile 同时成立，不查询号码是否真实签发。程序校验或 LLM 审核不通过时，正确字段和原始 seed 保持不变，只把当前失败字段的原值和具体拒绝原因交给 LLM 定向重生成；新值必须重新通过相同校验，government_id 还必须重新通过 LLM 审核。每个字段达到最大修复次数后仍不合格，才淘汰该 seed。
5. 检查所有正 IPF 目标单元是否有候选支撑，并检查每个边际单元是否至少有 `ceil(output_count × target)` 个候选可供唯一无放回抽样；缺失或容量不足时定向生成候选，并重新执行相同 seed 审核、PII 补全和修复流程。
6. 对候选池运行 IPF。IPF 必须达到配置的最大 L1 收敛阈值，且最大理想 inclusion probability 不得超过 1；否则整次生成失败。IPF 只调整候选权重，不生成或修改任何字段。
7. 按校准权重进行多次无放回抽样，保留总 L1 误差最小的样本；最终逐条执行完整 schema/字段校验并输出分布审计。最终样本的分布 L1 当前只作为报告，不另设阻塞阈值。

### 3.1 程序硬校验边界

- `name`：必须是非空字符串且不超过 120 个字符；不检查姓名全局唯一，也没有独立的姓名语义 reviewer。
- `email`：必须符合语法并使用 `example.com`、`example.org` 或 `example.net`；按忽略大小写的规范化值检查全局唯一。
- `phone_number`：必须使用以 `+` 开头的国际格式，数字总长度为 8—15；国家码与 national number 长度必须匹配 citizenship 或 current_location 中至少一国；去除格式字符后检查全局唯一。程序不检查号码是否真实存在。
- `government_id`：按 citizenship 选择已登记的国家格式，检查格式、长度、可解码的出生年份/日期、性别标记以及明显占位序列；规范化后检查全局唯一。当前实现没有各国 checksum 算法，也不查询真实签发记录。
- `check_profile`：检查 17 个必需字段、age 范围、枚举值、三个国家/地点是否属于配置集合、部分文本长度以及上述四个 PII 字段；跨字段人生经历的现实一致性由先前的 seed LLM audit 负责，不在最终程序校验中重复判断。

### 3.2 LLM 返回结构与修复

- Seed audit 的 batch 必须完整覆盖输入 candidate ID，不能缺失、重复或增加 ID；`decision` 只能是 `accept` 或 `reject`。当前程序不枚举 seed rejection 的 reason code，也不强制 accepted 记录的 reason 必须为空。
- PII completion 只消费 `name`、`email`、`phone_number` 和 `government_id`，忽略模型返回的其他字段，因此模型不能覆盖 13 个 seed 字段。
- 每个失败字段默认最多定向修复 3 次。修复 prompt 保留其他当前有效 PII；government_id 每次通过程序校验后都要重新进入 LLM 语义审核。
- 如果有效候选数量不足，程序最多按 `max_backfill_rounds` 继续补充候选；补充候选不会绕过任何审核。

字段修复统计记录首次通过数、首次失败数、修复尝试次数、按 profile 和按 attempt 的修复通过率、耗尽重试数；government_id 另行记录首次与修复阶段的 LLM 审核数、接受数、拒绝数、拒绝原因和语义审核耗尽数。LLM 统计记录各阶段请求数、候选项数、请求字段值数及服务端返回的 prompt/completion/total token。使用 `--diagnostics-dir` 时，即使最终数量不足，仍会写出 `pii_initial.jsonl`、`pii_repaired.jsonl`、`pii_errors.jsonl` 和 `pii_metrics.json`。

大规模运行使用固定 `--seed` 和 `--checkpoint-dir`。每个完成且通过结构校验的
LLM batch 都会立即写入 SQLite；使用相同生成参数追加 `--resume` 后，程序会验证
模型、seed、batch 大小、目标表和 IPF 参数，并复用已经完成的 batch。并发数
`--llm-workers` 与滚动窗口限速 `--llm-rpm` 不参与兼容签名，因此恢复时可根据
API 限流情况调整。`MY_MODEL_API_KEY` 是主凭据，`MY_MODEL_API_KEYS` 可提供
逗号分隔的额外凭据；请求在去重后的 key 列表间轮转，`--llm-rpm` 对每个 key
分别维护滚动窗口。若额度不同，`MY_MODEL_API_KEY_RPMS` 按“主 key、额外 key”
的顺序提供各自限制；达到额度的 key 会被跳过，剩余请求自动调度给仍有容量的
key。报告只输出 `key_1`、`key_2` 等匿名请求计数。

成功写入一次新的 `artifacts/profile_generation/<run>/` 后，程序会删除 `artifacts/profile_generation/` 下其他旧运行目录，只保留本次运行涉及的输出、报告和诊断。失败运行不会删除最近一次成功结果。

## 4. IPF 约束及来源

| IPF 约束 | 涉及字段 | 数据来源 | 转换方式 | 状态 |
| --- | --- | --- | --- | --- |
| country_population | current_location → current_country | UN DESA World Population Prospects 2024 | 取 2025 年、18—80 岁人口，在支持国家内归一化 | 26 国已接入 |
| age_x_sex | age → age_group、sex | UN DESA World Population Prospects 2024 | 聚合五岁年龄组到项目年龄段 | 已接入 |
| current_country_x_age_group_x_sex | current_country、age_group、sex | UN DESA World Population Prospects 2024 | 保留逐国 2025 年年龄性别结构 | 26 国已接入 |
| current_country_x_current_city_size | current_country、current_city_size | UN DESA World Urbanization Prospects 2025 File 21 | 对 50,000+ 城市聚合为七个规模档；档内按城市人口抽样 | 26 国、8,422 城市已接入 |
| country_x_education | current_country、education_level | World Bank WDI 教育成就指标 | 累积教育指标差分为八个互斥层级 | 26 国已接入 |
| country_x_religion | current_country、religious_belief | Pew Research Center Religious Composition by Country 2020 | 映射到七类 taxonomy | 已接入；权威但非官方统计 |
| current_country_x_citizenship_country | current_country、citizenship_country | UN DESA International Migrant Stock 2020 | 目的地—来源矩阵；国籍不可用时使用出生国代理并记录口径 | 已接入 |
| current_country_x_birth_country | current_country、birth_country | UN DESA International Migrant Stock 2020 | 目的地—来源矩阵；出生国不可用时使用国籍代理并记录口径 | 已接入 |
| current_country_x_sex_x_birth_migration_status | current_country、sex、birth_migration_status | UN DESA International Migrant Stock 2020 | destination×origin×sex 聚合为 local/foreign | 已接入 |
| current_country_x_sex_x_citizenship_migration_status | current_country、sex、citizenship_migration_status | UN DESA International Migrant Stock 2020 | 同上；保留 B/C mixed-basis caveat | 已接入 |
| current_country_x_ethnicity | current_country、ethnicity | UNData table 26、各国官方人口普查、UN DESA IMS 2020 | 兼容类别直接映射；缺少兼容表时用移民来源地区作宽类别代理 | 已接入；跨国可比性弱于其他约束 |
| country_x_income_level | current_country、income_level | World Bank Poverty and Inequality Platform、OECD 相对收入分类 | 按各国中位福利的 0.75、1.00、1.50、2.00 倍切分五档 | 已接入 |
| age_x_sex_x_relationship_status | age_group、sex、relationship_status | UNData table 23、UN WPP 2024 | 最近可用婚姻状态观测由 2025 年年龄性别人口加权 | 已接入 |
| current_country_x_age_group_x_sex_x_relationship_status | current_country、age_group、sex、relationship_status | UNData table 23、UN WPP 2024 | 保留逐国年龄性别关系表 | 26 国已接入 |
| current_country_x_relationship_status_x_employment_status | current_country、relationship_status、employment_status | ILOSTAT observed 年龄×性别×婚姻状态就业率、UNData table 23 | logit-calibration 后聚合，保持关系和就业边际 | 26 国已接入；德国、日本用 pooled relation |
| current_country_x_sex_x_occupation | current_country、sex、occupation_group | ILOSTAT 2025 modelled estimates、UN WPP 2024 | 就业人口比生成 None；IPF 校准 ISCO-08 父桶，桶内采样两位类别 | 26 国已接入 |
| current_country_x_age_group_x_sex_x_ilo_education_group | current_country、age_group、sex、ilo_education_group | ILOSTAT observed working-age population、WDI、WPP | raking 到年龄性别和教育边际 | 已接入 |
| current_country_x_age_group_x_sex_x_employment_status | current_country、age_group、sex、employment_status | ILOSTAT observed 年龄就业曲线、2025 modelled margin | logit-calibration 到国家×性别就业边际 | 已接入 |
| current_country_x_sex_x_ilo_education_group_x_employment_status | current_country、sex、ilo_education_group、employment_status | ILOSTAT observed 教育就业率 | 校准到一致的教育和就业边际 | 已接入 |
| current_country_x_ilo_education_group_x_occupation_group | current_country、ilo_education_group、occupation_group | ILOSTAT observed occupation×education | raking 到教育和职业边际 | 已接入 |
| current_country_x_age_stage_x_sex_x_occupation_group | current_country、age_stage、sex、occupation_group | ILOSTAT observed youth/adult×occupation | raking 到年龄阶段就业和职业边际 | 已接入；中国、日本、南非用 pooled relation |
| current_country_x_welfare_education_group_x_income_level | current_country、welfare_education_group、income_level | World Bank Global distribution of welfare 2022、WDI、PIP | 两条条件贫困线形成福利秩区间，区间内最大熵映射到五档相对收入；源组人口覆盖与正式边际相差超过 10 倍时整国改用区域关系 | 已接入；代理国逐国标记 |
| current_country_x_age_stage_x_income_level | current_country、age_stage、income_level | World Bank Global distribution of welfare 2022、WPP、PIP | youth/adult 条件福利关系用同一方法映射 | 已接入；6 国使用 OHI relation |
| current_country_x_age_group_x_sex_x_physical_condition | current_country、age_group、sex、physical_condition | IHME GBD 2023 Results Tool、UN WPP 2024 | 22 个 cause 的同国同年龄同性别 prevalence 按 WPP 聚合并转换为代表性单标签 | 26 国已接入 |
| current_country_x_age_group_x_sex_x_mental_condition | current_country、age_group、sex、mental_condition | IHME GBD 2023 Results Tool、UN WPP 2024 | 16 个精神/物质使用标签的 cause prevalence 按同一方法转换 | 26 国已接入 |

## 5. 数据来源

- UN WPP 2024：https://population.un.org/wpp/downloads
- UN WUP 2025 Cities：https://population.un.org/wup/downloads?tab=Cities
- WUP 2025 File 21：https://population.un.org/wup/assets/Download/Cities/WUP2025-F21-DEGURBA-Cities_Pop.xlsx
- World Bank Education：https://data.worldbank.org/indicator/SE.PRM.CUAT.ZS
- World Bank PIP API：https://api.worldbank.org/pip/v1
- UNData marital status table 23：https://data.un.org/Data.aspx?d=POP&f=tableCode%3A23
- UNData ethnicity table 26：https://data.un.org/Data.aspx?d=POP&f=tableCode%3A26
- UN DESA International Migrant Stock：https://www.un.org/development/desa/pd/content/international-migrant-stock
- ILOSTAT modelled estimates：https://ilostat.ilo.org/methods/concepts-and-definitions/ilo-modelled-estimates/
- ILOSTAT occupation data：https://rplumber.ilo.org/files/indicator/EMP_2EMP_SEX_OCU_NB_A.rds
- ILOSTAT employment-to-population ratio：https://rplumber.ilo.org/files/indicator/EMP_2WAP_SEX_AGE_RT_A.rds
- ILOSTAT age×education working-age population：https://rplumber.ilo.org/files/indicator/POP_XWAP_SEX_AGE_EDU_NB_A.rds
- ILOSTAT age×education employment：https://rplumber.ilo.org/files/indicator/EMP_TEMP_SEX_AGE_EDU_NB_A.rds
- ILOSTAT occupation×education：https://rplumber.ilo.org/files/indicator/EMP_TEMP_SEX_OCU_EDU_NB_A.rds
- ILOSTAT age stage×occupation：https://rplumber.ilo.org/files/indicator/EMP_TEMP_SEX_AGE_OCU_NB_A.rds
- ILOSTAT age×marital employment ratio：https://rplumber.ilo.org/files/indicator/EMP_DWAP_SEX_AGE_MTS_RT_A.rds
- World Bank Global distribution of welfare 2022：https://datacatalog.worldbank.org/search/dataset/0066601/global-distribution-of-welfare-2022
- Pew religious composition：https://www.pewresearch.org/religion/feature/religious-composition-by-country-2010-2020/
- WHO World mental health today 2025：https://iris.who.int/bitstream/handle/10665/382343/9789240113817-eng.pdf?sequence=1
