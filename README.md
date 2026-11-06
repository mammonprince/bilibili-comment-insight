\# B 站评论洞察助手



一个用 Python、Streamlit 和阿里云百炼搭建的本地应用。它读取已保存的 B 站主评论和楼中楼回复，支持单串分析，也能汇总前三串讨论中的共同话题。



汇总结果附有评论 ID 和原文引文。程序在展示前核对引文是否出现在对应评论里，并检查共同话题的两条引文是否来自不同讨论串。



\## 本地运行



```bash

python -m pip install streamlit requests

python -m streamlit run app.py

```



打开终端显示的本地地址，使用默认视频链接，点击「查看已保存资料」，进入「观众评论」。体验 AI 分析时，在页面输入自己的百炼兼容地址和 API Key。



当前版本使用本地样例演示。


The bundled comments are fictional demo data.
