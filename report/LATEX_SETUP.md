# LaTeX 编译环境

报告源文件尚未创建。编译环境已用 TeX Live 2026、latexmk 4.88、pdfLaTeX、BibTeX、IEEEtran 类及 IEEEtran 参考文献样式验证。

在本目录创建报告主文件 `report.tex` 和参考文献文件后，从本目录运行：

```sh
latexmk -pdf -interaction=nonstopmode -halt-on-error report.tex
```

`.latexmkrc` 将 PDF 和辅助文件写入 `build/`。编译结果为 `build/report.pdf`；`build/` 已由仓库 `.gitignore` 忽略。

现有 `Final_Report.docx` 是报告内容来源，准备环境不会修改它。图表素材位于 `../outputs/report_materials/`；最终引用及正文待后续整理。
