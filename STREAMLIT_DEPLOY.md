# Streamlit Cloud 部署说明

## 本地运行

```powershell
py -3 -m pip install -r requirements.txt
py -3 -m streamlit run app.py
```

## 密钥配置

不要把 `EM_API_KEY` 写进代码或提交到仓库。

本地开发：

```toml
# ~/.streamlit/secrets.toml
EM_API_KEY = "你的密钥"
```

Streamlit Cloud：

1. 把代码推到 GitHub 仓库
2. 在 Streamlit Cloud 里选择这个仓库和 `app.py`
3. 在 `Secrets` 面板里添加：

```toml
EM_API_KEY = "你的密钥"
```

## 公开访问

部署成功后，Streamlit 会给你一个 `streamlit.app` 的公网地址。
