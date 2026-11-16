# CBAM-GSC研究代码

当前动态建模入口为 [greenbook/README.md](greenbook/README.md)。

- `greenbook/`：生产、物料、逐年投资与寿命、贸易、CBAM、年度GSC出清、κ网格搜索和离线能源原型。
- `results/dynamic_structure_2026_2040/`：第一轮2026—2040合成结构运行、完整输入、独立核算与测试日志。不是2026真实数据校准结果。
- `replication_johnson2026_2040/`：2026外部实际基线的采集准备、映射和输入预检。
- `output/Johnson2026_2040_重建/`：已下载来源、候选容量池与尚待填充的真实数据合同。
- `review_artifacts/`：原阶段A审阅证据及回归测试工作目录。原审阅发现的修复状态见对应报告末尾。

依赖：Python 3.11及以上、NumPy、SciPy。推荐从本目录运行：

```sh
python -m greenbook.run_model --synthetic-demo --output results/dynamic_structure_2026_2040 --kappa-grid 0.25 0.5 0.75 1 1.25 1.5 2 3 4
python -m unittest discover -s greenbook/tests -v
```

真实数据仍需静态配平、证据校准及留出检验。输入中的scenario、derived、observed不能混用；软件求解成功不会自动将数据认证为实际状态。
