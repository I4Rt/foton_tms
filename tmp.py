import base64

def build_basic_auth_header(username: str, password: str) -> str:
    """Authorization: Basic base64(username:password)"""
    credentials = f"{username}:{password}"
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
    return f"Basic {encoded}"


print(build_basic_auth_header("git_admin", 'foton313'))