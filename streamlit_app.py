import streamlit as st

st.set_page_config(page_title="CHE Operational Dashboard", layout="wide")

home = st.Page("pages/home.py", title="Home", icon="🏠", default=True)
st.image('https://curioushathi.com/wp-content/uploads/2025/05/Curious-Hathi-Logo.png')
ticket_extractor = st.Page("pages/ticket_extractor.py", title="Ticket Extractor", icon="🎫")

# Add future tools to this list as you build them, e.g.:
# another_tool = st.Page("pages/another_tool.py", title="Another Tool", icon="🛠️")
# pg = st.navigation([home, ticket_extractor, another_tool])

pg = st.navigation([home, ticket_extractor])
pg.run()