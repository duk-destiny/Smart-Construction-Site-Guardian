"""全局主题（D1）：蓝灰现代化界面样式，统一注入各页面。"""

_THEME_CSS = """
<style>
:root {
  --hz-bg: #222531;          /* 侧边栏底 */
  --hz-panel: #2c303c;       /* 面板/卡片 */
  --hz-accent: #60a5fa;      /* 蓝紫强调 */
  --hz-text: #e6e8ee;        /* 主文字 */
  --hz-muted: #9299a8;       /* 次要文字 */
}
html, body, .stApp { background-color: #1b1e27; color: var(--hz-text); }
header[data-testid="stHeader"] { background-color: #1b1e27; }
.stSidebar { background-color: var(--hz-bg); }
div[data-testid="stToolbar"] { display: none; }

/* 主标题 */
h1, h2, h3 { color: var(--hz-text); font-weight: 700; }

/* 主按钮 */
.stButton > button[data-baseweb="button"][kind="primary"] {
  background-color: var(--hz-accent); color: #10131c; border: none; font-weight: 700;
}
.stButton > button[data-baseweb="button"] {
  border: 1px solid #3d4250; color: var(--hz-text); background: var(--hz-panel);
}
.stButton > button[data-baseweb="button"]:hover { border-color: var(--hz-accent); }

/* 容器卡片 */
[data-testid="stContainer"] { background: var(--hz-panel); border-radius: 10px; padding: 8px; }

/* 标签/输入 */
.stTextInput > div > div, .stSelectbox > div > div, .stDateInput > div > div {
  background: var(--hz-panel); color: var(--hz-text);
}
.stCaption, .stMarkdown caption { color: var(--hz-muted); }
</style>
"""


def apply_theme() -> None:
    """在各页面顶部调用以注入全局样式。"""
    import streamlit as st
    st.markdown(_THEME_CSS, unsafe_allow_html=True)
