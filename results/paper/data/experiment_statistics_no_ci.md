# PrivacyTrace Experiment Statistics (No CI)

Source files: `artifacts/paper/data/*.csv`, `artifacts/paper/data/experimental_summary.json`, `artifacts/profile_pool/generation_report.json`, `artifacts/profile_pool/profiles.jsonl`, `artifacts/trajectories/combined_audit.json`.

## 1. Dataset and Execution Scale

| stage | count | unit |
| --- | --- | --- |
| Synthetic profiles | 1000 | profiles |
| Structured candidates | 100000 | candidates |
| Validated candidate pool | 96538 | candidates |
| Scenarios | 4000 | tasks |
| Tool trajectories | 4000 | trajectories |
| Recorded tool events | 67950 | events |
| MCP executions | 66725 | executions |
| Cache hits | 1225 | events |
| Distinct planned tools | 115 | tools |
| Tool servers | 25 | servers |


Trajectory audit:

| metric | value |
| --- | --- |
| record_count | 4000 |
| unique_scenario_count | 4000 |
| unique_profile_count | 1000 |
| profiles_with_four_trajectories | 1000 |
| tool_record_count | 67950 |
| mcp_executed_record_count | 66725 |
| cache_hit_record_count | 1225 |
| transport_failure_record_count | 0 |
| content_failure_record_count | 13430 |
| mapping_error_count | 0 |
| passed | True |

Domain counts:

| domain | count |
| --- | --- |
| career_learning | 1000 |
| health | 1000 |
| shopping | 1000 |
| travel | 1000 |

Trajectory status counts:

| status | count |
| --- | --- |
| satisfied | 3634 |
| agent_error | 284 |
| cannot_continue | 80 |
| simulator_error | 2 |

Termination reasons:

| reason | count |
| --- | --- |
| simulator_satisfied | 3634 |
| stalled_tool_loop | 211 |
| simulator_cannot_continue | 80 |
| agent_runtime_error | 73 |
| simulator_output_error | 2 |

Tool outcomes:

| outcome | count |
| --- | --- |
| success | 54520 |
| empty | 7432 |
| application_error | 5787 |
| stalled_tool_loop | 211 |

Models used in trajectory generation:

| role | model | count |
| --- | --- | --- |
| agent | deepseek-v4-flash | 4000 |
| simulator | deepseek-v4-flash | 4000 |


## 2. Profile Generation / Data Engine Statistics

| metric | value |
| --- | --- |
| requested_count | 1000 |
| candidate_count | 100000 |
| base_seed_count | 100000 |
| seed_audit_record_count | 101779 |
| llm_seed_accepted | 98173 |
| llm_seed_rejected | 3606 |
| candidate_pool_size | 96538 |
| completion_accepted | 96538 |
| completion_rejected | 1635 |
| logical_screen_survival_rate | 0.9646 |
| completion_survival_rate | 0.9833 |
| backfill_rounds | 7 |
| backfill_generated | 1779 |
| final_invalid_profiles | 0 |

Seed rejection reasons:

| reason | count |
| --- | --- |
| other_clear_cross_field_contradiction | 3589 |
| education_occupation_mismatch | 8 |
| age_education_contradiction | 6 |
| age_education_occupation_contradiction | 1 |
| education_occupation_contradiction | 2 |

Seed proposal mix:

| item | value |
| --- | --- |
| target_ratio_requested | 0.9 |
| target_proposal_available | True |
| target_informed | 90054 |
| uniform | 9946 |
| targeted_backfill | 1779 |

PII validation by field:

| field | initial_total | initial_passed | initial_failed | initial_pass_rate | repair_rounds | repair_attempts | repair_attempt_passed | repaired_profiles | exhausted |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| name | 98173 | 98153 | 20 | 0.9998 | 1 | 20 | 20 | 20 | 0 |
| email | 98173 | 92200 | 5973 | 0.9392 | 7 | 6533 | 5973 | 5973 | 0 |
| phone_number | 98173 | 68532 | 29641 | 0.6981 | 17 | 47738 | 28187 | 28187 | 1454 |
| government_id | 98173 | 82386 | 15787 | 0.8392 | 10 | 18845 | 15598 | 15598 | 189 |

LLM usage in profile pool generation:

| metric | value |
| --- | --- |
| requests | 86147 |
| candidate_items | 609126 |
| requested_field_values | 1645271 |
| prompt_tokens | 100235696 |
| completion_tokens | 17119272 |
| total_tokens | 117354968 |

LLM usage by stage:

| stage | requests | candidate_items | requested_field_values |
| --- | --- | --- | --- |
| LLM seed audit | 8622 | 240096 | 720288 |
| LLM completion | 20284 | 101363 | 405452 |
| LLM PII repair: name | 4 | 20 | 20 |
| LLM PII repair: email | 1699 | 8446 | 8446 |
| LLM PII repair: phone_number | 9785 | 48804 | 48804 |
| LLM government ID consistency audit | 15207 | 125932 | 377796 |
| LLM PII repair: government_id | 11722 | 32725 | 32725 |
| LLM PII repair: government_id feedback-v2 | 18824 | 51740 | 51740 |

IPF summary:

| metric | value |
| --- | --- |
| iterations | 500 |
| converged | True |
| final max_l1 | 0.01562 |
| weight_min | 0.0 |
| weight_max | 29.52277845 |
| effective_sample_size | 52326.2015 |
| positive_weight_candidates | 90052 |


## 3. Final Profile Distribution

| metric | value |
| --- | --- |
| profiles | 1000 |
| age_min | 18 |
| age_max | 80 |
| age_mean | 42.90 |
| age_median | 41.0 |

Age bins:

| bin | count | share |
| --- | --- | --- |
| 18-24 | 155 | 15.5% |
| 25-34 | 214 | 21.4% |
| 35-44 | 203 | 20.3% |
| 45-54 | 167 | 16.7% |
| 55-64 | 139 | 13.9% |
| 65-80 | 122 | 12.2% |

sex:

| value | count | share |
| --- | --- | --- |
| male | 501 | 50.1% |
| female | 499 | 49.9% |

citizenship:

| value | count | share |
| --- | --- | --- |
| China | 270 | 27.0% |
| India | 261 | 26.1% |
| United States | 70 | 7.0% |
| Indonesia | 49 | 4.9% |
| Pakistan | 39 | 3.9% |
| Brazil | 33 | 3.3% |
| Nigeria | 25 | 2.5% |
| Bangladesh | 24 | 2.4% |
| Ethiopia | 22 | 2.2% |
| Egypt | 21 | 2.1% |
| Philippines | 21 | 2.1% |
| Japan | 20 | 2.0% |
| Mexico | 19 | 1.9% |
| France | 16 | 1.6% |
| Iran | 16 | 1.6% |
| Vietnam | 15 | 1.5% |
| Turkey | 14 | 1.4% |
| United Kingdom | 13 | 1.3% |
| South Korea | 13 | 1.3% |
| Germany | 12 | 1.2% |
| South Africa | 8 | 0.8% |
| Kenya | 6 | 0.6% |
| Colombia | 6 | 0.6% |
| Poland | 4 | 0.4% |
| Canada | 2 | 0.2% |
| Australia | 1 | 0.1% |

education_level:

| value | count | share |
| --- | --- | --- |
| vocational | 211 | 21.1% |
| none | 187 | 18.7% |
| lower secondary | 175 | 17.5% |
| primary | 167 | 16.7% |
| bachelor | 126 | 12.6% |
| upper secondary | 94 | 9.4% |
| master | 37 | 3.7% |
| doctorate | 3 | 0.3% |

income_level:

| value | count | share |
| --- | --- | --- |
| low | 303 | 30.3% |
| middle | 291 | 29.1% |
| lower-middle | 186 | 18.6% |
| high | 130 | 13.0% |
| upper-middle | 90 | 9.0% |

relationship_status:

| value | count | share |
| --- | --- | --- |
| married | 688 | 68.8% |
| single | 210 | 21.0% |
| widowed | 53 | 5.3% |
| divorced | 34 | 3.4% |
| in relationship | 15 | 1.5% |

religious_belief:

| value | count | share |
| --- | --- | --- |
| Unaffiliated | 319 | 31.9% |
| Hindu | 219 | 21.9% |
| Muslim | 209 | 20.9% |
| Christian | 195 | 19.5% |
| Buddhist | 29 | 2.9% |
| Folk | 28 | 2.8% |
| Jewish | 1 | 0.1% |

physical_condition:

| value | count | share |
| --- | --- | --- |
| None | 248 | 24.8% |
| tension-type headache | 188 | 18.8% |
| age-related and other hearing loss | 140 | 14.0% |
| migraine | 104 | 10.4% |
| chronic kidney disease | 69 | 6.9% |
| low back pain | 59 | 5.9% |
| type 2 diabetes | 48 | 4.8% |
| osteoarthritis | 44 | 4.4% |
| dermatitis | 24 | 2.4% |
| asthma | 22 | 2.2% |
| chronic obstructive pulmonary disease | 14 | 1.4% |
| stroke | 10 | 1.0% |
| gastritis and duodenitis | 8 | 0.8% |
| atrial fibrillation and flutter | 5 | 0.5% |
| Alzheimer's disease and other dementias | 5 | 0.5% |
| HIV/AIDS | 3 | 0.3% |
| Parkinson's disease | 2 | 0.2% |
| urticaria | 2 | 0.2% |
| alopecia areata | 2 | 0.2% |
| idiopathic epilepsy | 2 | 0.2% |
| psoriasis | 1 | 0.1% |

mental_condition:

| value | count | share |
| --- | --- | --- |
| None | 814 | 81.4% |
| anxiety disorders | 56 | 5.6% |
| depressive disorders | 41 | 4.1% |
| other mental disorders | 36 | 3.6% |
| idiopathic developmental intellectual disability | 14 | 1.4% |
| alcohol use disorders | 13 | 1.3% |
| schizophrenia | 6 | 0.6% |
| eating disorders | 4 | 0.4% |
| bipolar disorder | 4 | 0.4% |
| ADHD | 4 | 0.4% |
| autism spectrum disorders | 3 | 0.3% |
| opioid use disorders | 2 | 0.2% |
| cannabis use disorders | 1 | 0.1% |
| conduct disorder | 1 | 0.1% |
| cocaine use disorders | 1 | 0.1% |

occupation:

| value | count | share |
| --- | --- | --- |
| None | 390 | 39.0% |
| construction laborer | 22 | 2.2% |
| car washer | 20 | 2.0% |
| forestry laborer | 18 | 1.8% |
| garbage collector | 18 | 1.8% |
| fast-food preparer | 17 | 1.7% |
| commercial fisher | 17 | 1.7% |
| delivery courier | 15 | 1.5% |
| domestic cleaner | 15 | 1.5% |
| factory laborer | 12 | 1.2% |
| cashier | 12 | 1.2% |
| street vendor | 12 | 1.2% |
| crop farmer | 12 | 1.2% |
| firefighter | 12 | 1.2% |
| orchard farmer | 12 | 1.2% |
| subsistence fisher | 12 | 1.2% |
| farm laborer | 11 | 1.1% |
| security guard | 11 | 1.1% |
| hairdresser | 11 | 1.1% |
| forestry worker | 10 | 1.0% |
| subsistence farmer | 10 | 1.0% |
| retail salesperson | 10 | 1.0% |
| waiter | 9 | 0.9% |
| kitchen helper | 8 | 0.8% |
| home care aide | 8 | 0.8% |
| baker | 8 | 0.8% |
| market vendor | 7 | 0.7% |
| chemist | 7 | 0.7% |
| printer | 7 | 0.7% |
| office cleaner | 7 | 0.7% |
| butcher | 6 | 0.6% |
| welder | 6 | 0.6% |
| nursing aide | 6 | 0.6% |
| nonprofit director | 6 | 0.6% |
| chef | 6 | 0.6% |
| chemical plant operator | 6 | 0.6% |
| electronics assembler | 6 | 0.6% |
| CNC machine operator | 6 | 0.6% |
| accountant | 5 | 0.5% |
| potter | 5 | 0.5% |
| bus driver | 5 | 0.5% |
| police officer | 5 | 0.5% |
| secretary | 5 | 0.5% |
| electrician | 5 | 0.5% |
| biologist | 5 | 0.5% |
| tailor | 5 | 0.5% |
| power plant operator | 5 | 0.5% |
| electronics repairer | 5 | 0.5% |
| mechanical engineer | 4 | 0.4% |
| excavator operator | 4 | 0.4% |
| web technician | 4 | 0.4% |
| marketing specialist | 4 | 0.4% |
| secondary school teacher | 4 | 0.4% |
| factory manager | 4 | 0.4% |
| construction manager | 4 | 0.4% |
| university lecturer | 4 | 0.4% |
| primary school teacher | 4 | 0.4% |
| plumber | 4 | 0.4% |
| machinist | 4 | 0.4% |
| carpenter | 4 | 0.4% |
| financial analyst | 4 | 0.4% |
| human resources manager | 3 | 0.3% |
| registered nurse | 3 | 0.3% |
| vehicle assembler | 3 | 0.3% |
| restaurant manager | 3 | 0.3% |
| auto mechanic | 3 | 0.3% |
| land surveyor | 3 | 0.3% |
| mail clerk | 3 | 0.3% |
| bookkeeper | 3 | 0.3% |
| jeweler | 3 | 0.3% |
| IT support technician | 3 | 0.3% |
| lawyer | 3 | 0.3% |
| shipping clerk | 3 | 0.3% |
| sales manager | 3 | 0.3% |
| dentist | 3 | 0.3% |
| product assembler | 3 | 0.3% |
| civil engineer | 3 | 0.3% |
| bank teller | 3 | 0.3% |
| pharmacy technician | 3 | 0.3% |
| pharmacist | 3 | 0.3% |
| finance manager | 2 | 0.2% |
| travel clerk | 2 | 0.2% |
| network technician | 2 | 0.2% |
| inventory clerk | 2 | 0.2% |
| real estate agent | 2 | 0.2% |
| logistics manager | 2 | 0.2% |
| data entry clerk | 2 | 0.2% |
| data scientist | 2 | 0.2% |
| hotel manager | 2 | 0.2% |
| cybersecurity analyst | 2 | 0.2% |
| truck driver | 2 | 0.2% |
| appliance repairer | 2 | 0.2% |
| social work assistant | 2 | 0.2% |
| insurance agent | 2 | 0.2% |
| bricklayer | 2 | 0.2% |
| software developer | 2 | 0.2% |
| paralegal | 2 | 0.2% |
| library clerk | 2 | 0.2% |
| paramedic | 2 | 0.2% |
| physician | 2 | 0.2% |
| childcare worker | 2 | 0.2% |
| retail manager | 2 | 0.2% |
| medical laboratory technician | 1 | 0.1% |
| architect | 1 | 0.1% |
| payroll clerk | 1 | 0.1% |
| electrical engineer | 1 | 0.1% |
| photographer | 1 | 0.1% |
| civil engineering technician | 1 | 0.1% |
| government administrator | 1 | 0.1% |
| office clerk | 1 | 0.1% |
| receptionist | 1 | 0.1% |

current_location countries:

| value | count | share |
| --- | --- | --- |
| China | 270 | 27.0% |
| India | 260 | 26.0% |
| United States | 76 | 7.6% |
| Indonesia | 49 | 4.9% |
| Pakistan | 38 | 3.8% |
| Brazil | 32 | 3.2% |
| Nigeria | 24 | 2.4% |
| Bangladesh | 24 | 2.4% |
| Egypt | 21 | 2.1% |
| Ethiopia | 21 | 2.1% |
| Philippines | 21 | 2.1% |
| Japan | 20 | 2.0% |
| Mexico | 17 | 1.7% |
| France | 16 | 1.6% |
| Vietnam | 15 | 1.5% |
| Turkey | 15 | 1.5% |
| Iran | 15 | 1.5% |
| South Korea | 13 | 1.3% |
| United Kingdom | 12 | 1.2% |
| Germany | 12 | 1.2% |
| South Africa | 9 | 0.9% |
| Kenya | 6 | 0.6% |
| Colombia | 6 | 0.6% |
| Poland | 4 | 0.4% |
| Canada | 3 | 0.3% |
| Australia | 1 | 0.1% |

current_location cities (all 655 unique; top 30):

| value | count | share |
| --- | --- | --- |
| Shanghai, China | 15 | 1.5% |
| Guangzhou, China | 14 | 1.4% |
| Jakarta, Indonesia | 13 | 1.3% |
| Dhaka, Bangladesh | 11 | 1.1% |
| Los Angeles, United States | 10 | 1.0% |
| New Delhi, India | 10 | 1.0% |
| Manila, Philippines | 10 | 1.0% |
| Tōkyō (Tokyo), Japan | 8 | 0.8% |
| Beijing, China | 8 | 0.8% |
| Mumbai, India | 8 | 0.8% |
| Chennai, India | 8 | 0.8% |
| Chengdu, China | 7 | 0.7% |
| Shenzhen, China | 7 | 0.7% |
| Kolkata, India | 7 | 0.7% |
| Surat, India | 6 | 0.6% |
| São Paulo, Brazil | 6 | 0.6% |
| Bengaluru, India | 6 | 0.6% |
| Wuhan, China | 6 | 0.6% |
| Lahore, Pakistan | 6 | 0.6% |
| Paris, France | 5 | 0.5% |
| Seoul, South Korea | 5 | 0.5% |
| Al-Qahirah (Cairo), Egypt | 5 | 0.5% |
| Medan, Indonesia | 5 | 0.5% |
| Johannesburg, South Africa | 5 | 0.5% |
| New York City, United States | 5 | 0.5% |
| Nairobi, Kenya | 5 | 0.5% |
| Hangzhou, China | 5 | 0.5% |
| Shenyang, China | 5 | 0.5% |
| Rio de Janeiro, Brazil | 4 | 0.4% |
| Osaka, Japan | 4 | 0.4% |

birth_location countries:

| value | count | share |
| --- | --- | --- |
| China | 271 | 27.1% |
| India | 259 | 25.9% |
| United States | 70 | 7.0% |
| Indonesia | 50 | 5.0% |
| Pakistan | 38 | 3.8% |
| Brazil | 31 | 3.1% |
| Bangladesh | 26 | 2.6% |
| Nigeria | 24 | 2.4% |
| Philippines | 22 | 2.2% |
| Japan | 21 | 2.1% |
| Egypt | 21 | 2.1% |
| Ethiopia | 21 | 2.1% |
| Mexico | 18 | 1.8% |
| Vietnam | 17 | 1.7% |
| France | 15 | 1.5% |
| Iran | 15 | 1.5% |
| Turkey | 14 | 1.4% |
| South Korea | 12 | 1.2% |
| Germany | 12 | 1.2% |
| United Kingdom | 11 | 1.1% |
| South Africa | 8 | 0.8% |
| Kenya | 7 | 0.7% |
| Colombia | 7 | 0.7% |
| Poland | 5 | 0.5% |
| Canada | 4 | 0.4% |
| Australia | 1 | 0.1% |

birth_location cities (all 659 unique; top 30):

| value | count | share |
| --- | --- | --- |
| Shanghai, China | 17 | 1.7% |
| New Delhi, India | 16 | 1.6% |
| Kolkata, India | 15 | 1.5% |
| Guangzhou, China | 14 | 1.4% |
| Jakarta, Indonesia | 12 | 1.2% |
| Los Angeles, United States | 10 | 1.0% |
| Manila, Philippines | 9 | 0.9% |
| Paris, France | 7 | 0.7% |
| Mumbai, India | 7 | 0.7% |
| Shenzhen, China | 7 | 0.7% |
| Dhaka, Bangladesh | 7 | 0.7% |
| Seoul, South Korea | 6 | 0.6% |
| São Paulo, Brazil | 6 | 0.6% |
| Ahmedabad, India | 6 | 0.6% |
| Hajipur, India | 6 | 0.6% |
| Lahore, Pakistan | 6 | 0.6% |
| Karachi, Pakistan | 6 | 0.6% |
| Ādīs Ᾱbeba (Addis Ababa), Ethiopia | 6 | 0.6% |
| Bengaluru, India | 6 | 0.6% |
| Istanbul, Turkey | 5 | 0.5% |
| Wuhan, China | 5 | 0.5% |
| Fukuoka, Japan | 5 | 0.5% |
| Chennai, India | 5 | 0.5% |
| Al-Qahirah (Cairo), Egypt | 5 | 0.5% |
| Chongqing, China | 5 | 0.5% |
| Beijing, China | 5 | 0.5% |
| Nanning, China | 5 | 0.5% |
| Ho Chi Minh City, Vietnam | 5 | 0.5% |
| San Diego, United States | 4 | 0.4% |
| Tōkyō (Tokyo), Japan | 4 | 0.4% |


## 4. Main Attack Results

| arm | correct | slots | asr_pct |
| --- | --- | --- | --- |
| Schema-only prior | 12220 | 68000 | 17.971 |
| Full catalog | 17852 | 68000 | 26.253 |
| Raw repeated trace | 17954 | 68000 | 26.403 |
| Two-stage final | 17561 | 68000 | 25.825 |
| Four-domain aggregate | 5570 | 17000 | 32.765 |
| Single-server micro-average | 43862 | 180965 | 24.238 |


## 5. Trace Channel Ablation

| view | correct | slots | asr_pct |
| --- | --- | --- | --- |
| Schema-only prior | 12220 | 68000 | 17.971 |
| Metadata + sequence | 9879 | 68000 | 14.528 |
| Metadata + sequence + parameters | 18840 | 68000 | 27.706 |
| Metadata + sequence + results | 16685 | 68000 | 24.537 |
| Full catalog | 17852 | 68000 | 26.253 |

Channel effects without CI:

| effect | delta_percentage_points | bootstrap_samples |
| --- | --- | --- |
| Parameters over metadata | 13.178 | 10000 |
| Results over metadata | 10.009 | 10000 |
| Parameter-result interaction | -11.462 | 10000 |


## 6. Observer Scope

| contrast | delta_percentage_points | unit |
| --- | --- | --- |
| Single server vs paired schema | 6.235 | profile-balanced |
| All servers vs single server | 3.144 | profile-balanced |
| Four domains vs single-domain catalog | 6.512 | profile |

Single-server detailed results:

| server | views | slots | correct | unicode_false_positives_removed | asr_pct | lift_vs_paired_schema_percentage_points |
| --- | --- | --- | --- | --- | --- | --- |
| Geoapify | 1598 | 27166 | 8901 | 7 | 32.765 | 14.879 |
| Duffel Flight Search | 207 | 3519 | 1131 | 0 | 32.14 | 14.152 |
| Keenable Web Search | 865 | 14705 | 4651 | 1 | 31.629 | 13.573 |
| Weather Data | 828 | 14076 | 3814 | 1 | 27.096 | 9.286 |
| Time MCP | 594 | 10098 | 2692 | 0 | 26.659 | 8.596 |
| tickadoo | 438 | 7446 | 1956 | 0 | 26.269 | 8.3 |
| BioMCP | 626 | 10642 | 2418 | 0 | 22.721 | 5.149 |
| Wikipedia | 1673 | 28441 | 6435 | 0 | 22.626 | 4.895 |
| Remoote Jobs | 658 | 11186 | 2437 | 0 | 21.786 | 3.996 |
| Open Food Facts | 20 | 340 | 74 | 0 | 21.765 | 5.0 |
| Reddit | 69 | 1173 | 251 | 0 | 21.398 | 1.961 |
| Movie Recommender | 5 | 85 | 17 | 0 | 20.0 | -1.176 |
| Call for Papers | 68 | 1156 | 219 | 0 | 18.945 | -1.384 |
| Paper Search | 575 | 9775 | 1805 | 0 | 18.465 | 0.378 |
| National Parks | 588 | 9996 | 1843 | 0 | 18.437 | 0.4 |
| Car Price Evaluator | 391 | 6647 | 1193 | 0 | 17.948 | -0.978 |
| Medical Calculator | 342 | 5814 | 1043 | 0 | 17.939 | -0.654 |
| Hugging Face | 284 | 4828 | 827 | 0 | 17.129 | -1.098 |
| Metropolitan Museum | 124 | 2108 | 357 | 0 | 16.935 | -1.756 |
| Game Trends | 199 | 3383 | 554 | 0 | 16.376 | -1.419 |
| Context7 | 65 | 1105 | 180 | 0 | 16.29 | -1.991 |
| DailyMed MCP | 208 | 3536 | 546 | 0 | 15.441 | -2.772 |
| FruityVice | 162 | 2754 | 401 | 0 | 14.561 | -3.159 |
| OneBusAway | 19 | 323 | 46 | 0 | 14.241 | -2.168 |
| OpenFDA | 39 | 663 | 71 | 0 | 10.709 | -6.636 |


## 7. Domain Results

| domain | catalog_asr_pct | lift_percentage_points |
| --- | --- | --- |
| Career learning | 21.571 | 3.6 |
| Health | 23.247 | 5.4 |
| Shopping | 31.865 | 13.718 |
| Travel | 28.329 | 10.412 |


## 8. Attribute-Level Results

| attribute | schema_asr_pct | catalog_correct | catalog_slots | catalog_asr_pct | lift_percentage_points |
| --- | --- | --- | --- | --- | --- |
| citizenship | 7.05 | 2309 | 4000 | 57.725 | 50.675 |
| ethnicity | 9.8 | 1994 | 4000 | 49.85 | 40.05 |
| current location | 0.25 | 1536 | 4000 | 38.4 | 38.15 |
| religious belief | 20.4 | 2001 | 4000 | 50.025 | 29.625 |
| age | 2.825 | 620 | 4000 | 15.5 | 12.675 |
| physical condition | 24.8 | 1213 | 4000 | 30.325 | 5.525 |
| occupation | 0.65 | 226 | 4000 | 5.65 | 5.0 |
| birth location | 0.3 | 57 | 4000 | 1.425 | 1.125 |
| name | 0.0 | 34 | 4000 | 0.85 | 0.85 |
| phone number | 0.0 | 1 | 4000 | 0.025 | 0.025 |
| email | 0.0 | 0 | 4000 | 0.0 | 0.0 |
| government ID | 0.0 | 0 | 4000 | 0.0 | 0.0 |
| education level | 12.6 | 410 | 4000 | 10.25 | -2.35 |
| sex | 50.1 | 1849 | 4000 | 46.225 | -3.875 |
| income level | 29.1 | 1001 | 4000 | 25.025 | -4.075 |
| mental condition | 81.4 | 3065 | 4000 | 76.625 | -4.775 |
| relationship status | 66.225 | 1536 | 4000 | 38.4 | -27.825 |


## 9. Call Count Effects

| visible_calls | view_count | profile_count | catalog_correct | catalog_slots | catalog_asr_pct | paired_schema_correct | paired_schema_slots | paired_schema_asr_pct | lift_percentage_points | unicode_false_positives_removed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 55 | 54 | 130 | 935 | 13.904 | 171 | 935 | 18.289 | -4.385 | 0 |
| 1--5 | 540 | 453 | 1916 | 9180 | 20.871 | 1652 | 9180 | 17.996 | 2.875 | 0 |
| 6--10 | 866 | 626 | 3583 | 14722 | 24.338 | 2697 | 14722 | 18.32 | 6.018 | 1 |
| 11--20 | 1479 | 839 | 7036 | 25143 | 27.984 | 4523 | 25143 | 17.989 | 9.995 | 1 |
| 21--40 | 948 | 674 | 4716 | 16116 | 29.263 | 2839 | 16116 | 17.616 | 11.647 | 2 |
| 41+ | 101 | 100 | 471 | 1717 | 27.432 | 307 | 1717 | 17.88 | 9.552 | 0 |


## 10. Representation Diagnostic

| representation | correct | slots | asr_pct | prompt_tokens_p50 | prompt_tokens_p90 | prompt_chars_p50 | prompt_chars_p90 | generation_failed_views |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Lossless catalog | 17852 | 68000 | 26.253 | 30393 | 92271 | 117011 | 340217 | 11 |
| Raw repeated trace | 17954 | 68000 | 26.403 | 37520 | 107413 | 137306 | 384869 | 13 |


## 11. Two-Stage Attack

| measure | correct | slots | asr_pct | coverage_slots | precision_pct |
| --- | --- | --- | --- | --- | --- |
| Stage 1 inferred-only | 5324 | 68000 | 7.829 | 11600 | 45.897 |
| Stage 2 on Stage 1-unresolved slots | 12237 | 56179 | 21.782 |  |  |
| Schema prior on same unresolved slots | 10384 | 56179 | 18.484 |  |  |
| Two-stage final | 17561 | 68000 | 25.825 |  |  |

Additional two-stage statistics:

| metric | value |
| --- | --- |
| stage2_minus_schema_on_unresolved_observed_delta_percentage_points | 3.298 |
| stage2_minus_schema_on_unresolved_profile_clustered_delta_percentage_points | 3.381 |
| one_shot_full_catalog_asr_pct | 26.253 |
| two_stage_final_asr_pct | 25.825 |
| one_shot_minus_two_stage_percentage_points | 0.428 |
| correct_under_both | 13163 |
| correct_only_one_shot | 4689 |
| correct_only_two_stage | 4398 |
| wrong_under_both | 45750 |
| correctness_agreement_pct | 86.64 |


## 12. Model Generalization and Cost

| model | correct | slots | asr_pct | generation_failed_views | unicode_false_positives_removed |
| --- | --- | --- | --- | --- | --- |
| GPT-5.5 | 18888 | 68000 | 27.776 | 87 | 16 |
| DeepSeek-V4-Flash-0731 | 18358 | 68000 | 26.997 | 11 | 4 |
| MiniMax-M3 | 17196 | 68000 | 25.288 | 12 | 6 |
| Gemini-3-Flash-Preview | 19254 | 68000 | 28.315 | 19 | 0 |

Cost/token details:

| model | views | input_tokens | estimated_output_tokens | total_tokens_est | input_price_per_m | output_price_per_m | price_basis | estimated_cost_usd | estimated_cost_per_1000_views_usd | asr_pct | failed_views |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| GPT-5.5 | 4000 | 151968682 | 272902 | 152241584 | 5.0 | 30.0 | standard | 768.0304699999999 | 192.00761749999998 | 27.776 | 87 |
| DeepSeek-V4-Flash-0731 | 4000 | 195051907 | 236939 | 195288846 | 0.22 | 0.66 | off-peak | 43.067799279999996 | 10.766949819999999 | 26.997 | 11 |
| MiniMax-M3 | 4000 | 190285033 | 366951 | 190651984 | 0.3 | 1.2 | standard <=512k | 57.5258511 | 14.381462775 | 25.288 | 12 |
| Gemini-3-Flash-Preview | 4000 | 220881612 | 221322 | 221102934 | 0.5 | 3.0 | paid tier | 111.104772 | 27.776193 | 28.315 | 19 |
