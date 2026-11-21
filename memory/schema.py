# DFCP 记忆模块 v0.2 — 领域树 v1.5 + 判定谓词 + 保护清单（唯一权威源）
# 设计冻结：docs/design_memory_v02.md + 领域树三轮盲测定稿（logs/ds_tag_retrieval_sim*.txt）
# 规则：树节点是合法编码集合，Op1 入库只认编码（v1.4-2）；语义聚类仅离线提案不直接写树

DOMAIN_TREE = {
    "001": {
        "name": "经济学",
        "alias": "宏观投资",
        "children": {
            "001": "宏观指标解读", "002": "事件冲击推演", "003": "持仓与仓位决策",
            "004": "市场复盘", "005": "情绪纪律",
        },
    },
    "002": {
        "name": "计算机科学",
        "alias": "技术实现",
        "children": {
            "001": "架构设计", "002": "代码实现", "003": "数据与语料",
            "004": "调试与根因", "005": "构建与环境", "006": "硬件与车载底层",
        },
    },
    "003": {
        "name": "产品设计",
        "alias": "需求与HMI",
        "children": {
            "001": "需求理解", "002": "UI还原", "003": "车机HMI规范", "004": "用户问题修复",
        },
    },
    "004": {
        "name": "组织行为学",
        "alias": "工作协作",
        "children": {
            "001": "项目管理", "002": "跨部门沟通", "003": "问题单流转",
        },
    },
    "005": {
        "name": "认知科学",
        "alias": "元协作",
        "children": {
            "001": "设计裁决", "002": "用户洞察", "003": "记忆自我管理",
            "004": "元思域", "005": "失误与纠错", "006": "方案攻防与专利评审",
            "007": "世界模型与因果实验", "008": "自我锚定与利害结算",
        },
    },
    "000": {"name": "待分类", "alias": "待分类", "children": {"001": "临时待归类"}},
}

# 行为树（2位骨架码，专利B冻结口径；L3行为细分按需扩展）
BEHAVIOR_TREE = {
    "Aa": "G00 发散", "Bb": "G01 意图", "Bc": "G02 最弱环", "Bd": "G03 双层",
    "Be": "G04 信源核查", "Cf": "G05 三步分馏", "Cg": "G06 利益者全景",
    "Ch": "G07 多维校验", "Di": "G08 溯源", "Dj": "G09 剥离", "Dk": "G10 锚定",
    "Dl": "G11 自反校准", "Em": "G12 暗共识", "En": "G13 跨时间轴",
    "Eo": "G14 等价识别", "Fp": "G15 量化", "Fq": "G16 出清", "Fr": "G17 低响应",
    "Gs": "G18 破壁", "Ht": "G22 决策整合", "Iu": "G19 风险刹停", "Iv": "G20 零延迟",
    "Gz": "G26 回退", "Jw": "G21 复盘固化", "B00": "量级判定",
}

# 关联树（7类边白名单，v1.4-5：禁止自由文本边）
RELATION_TREE = {
    "P": "前驱", "C": "并行", "F": "反馈", "H": "对冲",
    "D": "derived_from", "Q": "回挂", "X": "跨域同构", "S": "压制",
}

# 置信度五级（L0-L4，与可复现性三级正交：管理推理 vs 管存储）
CONFIDENCE_LEVELS = {"L0", "L1", "L2", "L3", "L4"}

# 资产类别（2026-11-21 P2-1 新增枚举校验）：core_memory_card.asset_class 与
# memory_organize.add(asset_class=) 取值。历史曾混入 observation/insight/
# high_value/capability_pact/domain_tool 等业务分类(绿卡写入源硬编码所致),
# 已由 schema_migrate 迁移留档, 现收敛为两值。扩展须走裁决并同步此处。
ASSET_CLASS_ENUM = {"corpus", "runtime"}

# 可复现性三级判定（专利A）：决定对结构三层的占用权限（嵌套非映射，§4.7）
REPRO_TIERS = {
    "flash":       "三级不可复现 → summary+detail+source 全量永久",
    "situational": "二级条件可复现 → summary+detail；source 只存受限源入口",
    "derived":     "一级完全可复现 → 仅summary；source=检索路径指针",
}

# 记忆分类五维（2026-11-10 件②用户终裁：升格为 dfcp_tag 第五维枚举坐标）
# 定位（S015 原始约定，钉死）：五类=高价值、主动参与蒸馏/归纳/经验复用/复盘检索的特殊记忆角色，
#   不是每条入库记忆的万能分类。null 是合法取值=普通记录，不属于专项经验资产。
# 配套规则（2026-11-10 用户裁决）：
#   - 空值≠非法值：校验只拦白名单外的自定义字符串；null 放行
#   - 无 other/miscellaneous 兜底类（S015 裁定：封闭清单+万能兜底会稀释五类筛选价值）
#   - 不随便扩充枚举：新增一类须走完整裁决，同步四处（本枚举/judge Q3/design_memory_v02 §7.6/E4巡检口径）
#   - behavior 先验仅作 Q3 可疑提示（软先验非硬拦截——字面枚举不可靠，结构性判据才可靠）
# 与 dfcp_tag 其余四维正交：domain/behavior/relation 管"思维坐标"，memory_class 管"记忆资产角色"
# 判定样例（文档契约，不写死代码逻辑，口径漂移时以此校准）：
#   strategy_evolve      架构路线、范式切换、顶层裁决（例：别名v2六条件三层防御定案）
#   skill_refine         算子、阈值、流程微调、工程优化（例：E4第9项扩展失效别名计数）
#   scar_case            事故、bug、失效、错误方案复盘（例：首日泛指查询绕门事故）
#   relation_milestone   实体绑定、跨记忆关联、认知链路打通（例：分布式架构=CIPP重新唤起的血缘发现）
#   key_pact             规约、裁决、接口契约、准入规则（例：SPI调试模块为8397专属边界约束）
#   null（合法空）        普通记录：聊天存档/临时观测/中间读数/随手备注——照常参与普通语义召回，
#                        不进专项聚类/复盘流水线，维度匹配分归零不扣分
# behavior 软先验参考（judge Q3 可疑提示用，非硬约束）：
#   scar_case→Dl/Bc | skill_refine→Jw/Cf | strategy_evolve→Jw/Ht（Ht=决策整合更贴顶层裁决）
#   relation_milestone→Em/Eo | key_pact→Ht/Iv
# 导入侧防御备注(2026-11-16 终审裁决): 一次性语料导入脚本曾硬编码
# entity_history 类名导致 10 条拒收(11-13, 源头脚本已消亡零命中); 后续任何
# 批量导入必须先用 MEMORY_CLASS_ENUM/REPRO_TIERS 校验标签, 禁止自造枚举值。
MEMORY_CLASS_ENUM = {
    "strategy_evolve", "skill_refine", "scar_case",
    "relation_milestone", "key_pact",
}

# L3 场景启用门槛L3 场景启用门槛：≥5 条有效回挂才转正（有效回挂=编码被写进新条目，检索命中不算）
L3_PROMOTION_THRESHOLD = 5

# 000 待分类时限：挂 2 个整理周期，超期强制归类或删除
UNCLASSIFIED_MAX_CYCLES = 2

# 边界判定谓词（入库自动归类用；复盘消歧在最前）
PREDICATES = [
    ("复盘对象=市场结果/仓位", "001-004"),
    ("复盘对象=代码/工程过程", "002-004"),
    ("复盘对象=认知活动本身", "005-005"),
    ("工程Bug/架构/代码/构建/硬件/环境", "002"),
    ("投资/宏观/持仓/市场/情绪纪律", "001"),
    ("需求/HMI/UI/用户问题单", "003"),
    ("排期/协作/跨部门/问题单流转", "004"),
    ("裁决/纠错/元思域/AI失误/记忆治理/攻防/自我锚定", "005"),
]


def validate_domain(code: str) -> tuple[bool, str]:
    """领域编码校验：'域-子域[-场景]'，场景位可缺省（=L3未启用态，非错误）。"""
    parts = code.split("-")
    if len(parts) not in (2, 3):
        return False, f"编码段数错误: {code}（应为 域-子域[-场景]）"
    d, s = parts[0], parts[1]
    if d not in DOMAIN_TREE:
        return False, f"非法域码: {d}"
    if s not in DOMAIN_TREE[d]["children"]:
        return False, f"非法子域码: {d}-{s}"
    if len(parts) == 3 and parts[2] not in DOMAIN_TREE[d]["children"]:
        return False, f"非法场景码: {parts[2]}（L3未启用时编码位缺省，不要伪造场景位）"
    return True, ""


def validate_behavior(code: str) -> tuple[bool, str]:
    return (code in BEHAVIOR_TREE, "" if code in BEHAVIOR_TREE else f"非法骨架码: {code}")


def validate_relation(code: str) -> tuple[bool, str]:
    return (code in RELATION_TREE, "" if code in RELATION_TREE else f"非法关联码: {code}（7类白名单）")


def validate_memory_tags(tags: dict) -> tuple[bool, list[str]]:
    """Op1 前置编码校验（v1.4-4 护栏①）：任一位非法即拒收进隔离队列，不进主记录。"""
    errors = []
    for field, validator in [("domain", validate_domain), ("behavior", validate_behavior), ("relation", validate_relation)]:
        if field not in tags:
            errors.append(f"缺 {field} 字段")
            continue
        ok, msg = validator(tags[field])
        if not ok:
            errors.append(msg)
    if tags.get("confidence") not in CONFIDENCE_LEVELS:
        errors.append(f"非法置信度: {tags.get('confidence')}")
    if tags.get("repro") and tags.get("repro") not in REPRO_TIERS:
        errors.append(f"非法可复现性: {tags['repro']}（缺省由 judge_repro 判定）")
    # memory_class（件②，2026-11-10 用户终裁）：dfcp_tag 第五维枚举坐标。空值≠非法值：null 合法（=普通记录，S015 定位：非万能分类）；只拦白名单外字符串；无 other 兜底类。judge Q3 对"内容明显属五类但空"打 class_missing_suspect 交人工，不卡入库。
    # 刻意不做默认值——强制每次入库过"这算哪类"判定动作，不静默默认（Jw 评估固化形式）
    mc = tags.get("memory_class")
    if mc is not None and mc not in MEMORY_CLASS_ENUM:
        errors.append(f"非法memory_class: {mc}（五类枚举白名单，扩展规则见 schema 注释契约）")
    # asset_class（2026-11-21 P2-1）：缺省不校验（兼容不传的调用）；传了则须在白名单。
    ac = tags.get("asset_class")
    if ac is not None and ac not in ASSET_CLASS_ENUM:
        errors.append(f"非法asset_class: {ac}（仅 corpus|runtime）")
    return (len(errors) == 0, errors)
