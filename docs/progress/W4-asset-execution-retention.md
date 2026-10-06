# 整组资产原件持久化与实际迁移

0013_full_asset_execution 只新增3表：portfolio原请求/hash/期限、严格1..4子batch/action/key/目录绑定、独立明确consent原证据副本。对应db/full_models.py三个新类，全schema35typed/36physical。旧32typed表与历史hash不改；正式库未迁移或reset。

INSERT须actual OPEN owner epoch/原parent/未到期，child在Action创建前有固定UUID；ProductCatalogVersion是GLOBAL目录，仅真实catalog FK，不捏造owner字段。consent不用当前EvidenceFK阻正式reset保留：原ID/hash/copy留存，当前OPEN仍须实Evidence+audit验真；SEALED缺currentEvidence不伪称原archive proof。UPDATE/DELETE/TRUNCATE皆拒，downgrade有任意原件时拒。

真实test_full_asset_execution_migration.py node在首混合 W4/actual-whole-asset-additive-schema-and-local-owner-20261005T205943Z-2f2e659c PASS：0012→0013旧行逐行不变、空schema降回0012完全原snapshot、重新升0013/Base metadata一致、实际目录来源绑定、SCHEMA_ONLY非金融 fixture、所有3表更新/删除/截断/hash更改/批号重复/缺parent/过期/倒时序/重复银行key拒、全物理snapshot原件不变、拒有历史downgrade。该首manifest FAILED1PASS/1FAIL23.95s（身份Cookie fixture失败），不能记整批通过；范围稳定/全源独立变化false。身份后单节点修后PASS另记。

Root shared phase1和独立BANK首次newaccept接consumer，且按持久Batch actionID查关联，移除marker或new银行key不能降级；原已有bank operation只允许原恢复，不把当前授权重发为新银行效果。asset消费者必要HTTP UUID、wholeconsent by-key和execute固定batch合同正在窄补；此迁移PASS不证明真实资产执行/所有期限/全量验收。旧0012 migration test按旧版本Base新增表deltas以及旧test_migrations22表hardcoded全量检查尚须在相关验收修正，原记录不删。
