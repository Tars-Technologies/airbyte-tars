from http.cookies import SimpleCookie


def format_cookies(cookies):
    cookie = SimpleCookie()
    cookie.load(cookies)
    final_cookies = {}
    for key, morsel in cookie.items():
        final_cookies[key] = morsel.value
    return final_cookies
