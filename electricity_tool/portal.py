from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import copy
from html.parser import HTMLParser
from http.cookiejar import CookieJar
import re
import ssl
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse, urlunparse
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener

from .models import MeterAccount


class PortalError(RuntimeError):
    pass


class AuthenticationError(PortalError):
    pass


class ProfileAccessError(PortalError):
    pass


@dataclass
class LoginForm:
    action: str
    fields: dict[str, str]


class LoginFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[LoginForm] = []
        self._current: LoginForm | None = None
        self._has_password = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "form":
            self._current = LoginForm(values.get("action", ""), {})
            self._has_password = False
        elif tag.lower() == "input" and self._current is not None:
            name = values.get("name")
            if name:
                self._current.fields[name] = values.get("value", "")
            if values.get("type", "").lower() == "password":
                self._has_password = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form" and self._current is not None:
            if self._has_password:
                self.forms.append(self._current)
            self._current = None
            self._has_password = False


def parse_login_form(html: str) -> LoginForm:
    parser = LoginFormParser()
    parser.feed(html)
    if not parser.forms:
        raise AuthenticationError("The PNPSCADA login form was not found")
    return parser.forms[0]


@dataclass(frozen=True)
class PageLink:
    href: str
    text: str


class NavigationParser(HTMLParser):
    """Collect safe navigation targets from legacy PNPSCADA pages."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[PageLink] = []
        self.frames: list[str] = []
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        lowered = tag.lower()
        if lowered == "a":
            self._href = values.get("href") or None
            self._parts = []
            onclick = values.get("onclick", "")
            self.frames.extend(_script_targets(onclick))
        elif lowered in {"frame", "iframe"} and values.get("src"):
            self.frames.append(values["src"])

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append(PageLink(self._href, " ".join("".join(self._parts).split())))
            self._href = None
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)


class ProfileGraphParser(HTMLParser):
    """Extract PNPSCADA's website-only meter selector and selected utility."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.account_options: dict[str, str] = {}
        self.selected_utility: str | None = None
        self._in_account_select = False
        self._option_value: str | None = None
        self._option_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        lowered = tag.lower()
        identity = (values.get("id") or values.get("name") or "").lower()
        if lowered == "select" and identity == "pnpentid":
            self._in_account_select = True
        elif lowered == "option" and self._in_account_select:
            self._option_value = values.get("value") or None
            self._option_parts = []
        elif lowered == "input" and identity == "selgname_utility":
            self.selected_utility = values.get("value") or None

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "option" and self._in_account_select and self._option_value:
            name = " ".join("".join(self._option_parts).split())
            if name:
                self.account_options[name] = self._option_value
            self._option_value = None
            self._option_parts = []
        elif lowered == "select" and self._in_account_select:
            self._in_account_select = False

    def handle_data(self, data: str) -> None:
        if self._option_value is not None:
            self._option_parts.append(data)


def parse_profile_graph(html: str) -> ProfileGraphParser:
    parser = ProfileGraphParser()
    parser.feed(html)
    return parser


def _script_targets(text: str) -> list[str]:
    patterns = [
        r"(?:window\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)",
        r"(?:window\.)?location\.replace\(\s*['\"]([^'\"]+)",
        r"window\.open\(\s*['\"]([^'\"]+)",
    ]
    output: list[str] = []
    for pattern in patterns:
        output.extend(re.findall(pattern, text, flags=re.IGNORECASE))
    return output


def _extract_memh(html: str) -> str | None:
    patterns = [
        r"name\s*=\s*['\"]memh['\"][^>]*value\s*=\s*['\"]([^'\"]+)",
        r"[?&]memh=([-0-9]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _looks_like_login_page(html: str) -> bool:
    lowered = html.lower()
    return "id='loginform'" in lowered or 'id="loginform"' in lowered or (
        "please enter your login name and password" in lowered and "lpwd" in lowered
    )


def _looks_like_auth_error(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "login or password not specified",
            "access denied. please log in again",
            "incorrect login",
            "incorrect password",
            "invalid login",
            "login failed",
            "failed login",
            "wrong password",
            "authentication failed",
        )
    )


class PortalClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: int = 60,
        api_port: int = 441,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("The portal URL must be a valid HTTPS URL")
        self.timeout_seconds = timeout_seconds
        self.progress = progress or (lambda _message: None)
        self.cookies = CookieJar()
        self.api_cookies = CookieJar()
        context = ssl.create_default_context()
        # Recent Python/OpenSSL builds enable X.509 strict mode by default.
        # PNPSCADA's otherwise trusted chain has a legacy Basic Constraints
        # encoding that strict mode rejects. Clearing only the strict flag keeps
        # certificate authority, hostname, expiry, and TLS validation enabled.
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        self.opener = build_opener(HTTPCookieProcessor(self.cookies), HTTPSHandler(context=context))
        self.api_opener = build_opener(
            HTTPCookieProcessor(self.api_cookies), HTTPSHandler(context=context)
        )
        host = parsed.hostname
        self.api_base_url = urlunparse(("https", f"{host}:{api_port}", "", "", "", ""))
        self.username: str | None = None
        self._password: str | None = None
        self.memh: str | None = None
        self.last_url: str | None = None
        self.authenticated_start_url: str | None = None
        self.authenticated_start_html: str | None = None
        self.diagnostics: dict[str, tuple[str, str]] = {}

    def _request(
        self,
        url: str,
        data: dict[str, str] | None = None,
        method: str | None = None,
    ) -> str:
        body = urlencode(data).encode("utf-8") if data is not None else None
        request = Request(
            url,
            data=body,
            method=method or ("POST" if body is not None else "GET"),
            headers={
                "User-Agent": "ElectricityReadingTool/0.1 (+local authenticated PNPSCADA extractor)",
                "Accept": "text/html, application/xhtml+xml, application/xml, text/csv, */*",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            is_api_listener = urlparse(url).netloc == urlparse(self.api_base_url).netloc
            opener = self.api_opener if is_api_listener else self.opener
            with opener.open(request, timeout=self.timeout_seconds) as response:
                self.last_url = response.geturl()
                raw = response.read()
                charset = response.headers.get_content_charset() or "iso-8859-1"
                return raw.decode(charset, errors="replace")
        except HTTPError as exc:
            detail = exc.read(500).decode("utf-8", errors="replace")
            raise PortalError(f"PNPSCADA returned HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise PortalError(f"Could not connect to PNPSCADA: {exc.reason}") from exc

    def login(self, username: str, password: str) -> None:
        self.progress("Opening the PNPSCADA login page...")
        launch_url = urljoin(self.base_url + "/", "launch?context=gts")
        login_page = self._request(launch_url)
        form = parse_login_form(login_page)
        fields = dict(form.fields)
        fields.update(
            {
                "lusr": username,
                "lpwd": password,
                "context": fields.get("context", "gts"),
                "dohttps": fields.get("dohttps", "true"),
                "GMTnow": str(int(time.time() * 1000)),
            }
        )
        self.memh = fields.get("memh") or _extract_memh(login_page)
        action_url = urljoin(launch_url, form.action or "/_Login")
        self.progress("Signing in with the supplied portal account...")
        result = self._request(action_url, fields, method="POST")
        if _looks_like_login_page(result) or _looks_like_auth_error(result):
            raise AuthenticationError("PNPSCADA rejected the username or password")
        self.memh = _extract_memh(result) or self.memh
        self.username = username
        self._password = password
        self.authenticated_start_url = self.last_url or action_url
        self.authenticated_start_html = result
        # Cookies do not include a port. Keep a separate copy for the :441
        # listener so its JSESSIONID cannot overwrite the interactive portal
        # session later used to download the daily report page.
        for cookie in self.cookies:
            self.api_cookies.set_cookie(copy.copy(cookie))
        self.progress("PNPSCADA login completed.")

    def _record_diagnostic(self, name: str, url: str, text: str) -> None:
        key = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "page"
        candidate = key
        suffix = 2
        while candidate in self.diagnostics:
            candidate = f"{key}_{suffix}"
            suffix += 1
        self.diagnostics[candidate] = (url, text)

    def _profile_candidates(self) -> list[str]:
        path = "getMeterAccountProfile.jsp"
        return [urljoin(self.base_url + "/", path), urljoin(self.api_base_url + "/", path)]

    @staticmethod
    def _is_success_profile(text: str) -> bool:
        lowered = text.lower()
        return "<meter_account" in lowered and "<sample" in lowered and not _looks_like_auth_error(text)

    def fetch_account_profile(
        self,
        account: MeterAccount,
        start_date: date,
        end_date_inclusive: date,
    ) -> tuple[str, str]:
        if self.username is None or self._password is None:
            raise AuthenticationError("Log in before requesting meter profiles")
        exclusive_end = end_date_inclusive + timedelta(days=1)
        params = {
            "eids": account.eid,
            "eid": account.eid,
            "start": datetime.combine(start_date, datetime.min.time()).strftime("%Y-%m-%d %H:%M:%S"),
            "end": datetime.combine(exclusive_end, datetime.min.time()).strftime("%Y-%m-%d %H:%M:%S"),
        }
        last_response = ""
        errors: list[str] = []
        for endpoint in self._profile_candidates():
            query_url = endpoint + "?" + urlencode(params)
            try:
                last_response = self._request(query_url)
                self._record_diagnostic(
                    f"profile_{account.eid}_{urlparse(endpoint).port or 443}.xml",
                    endpoint,
                    last_response,
                )
                if self._is_success_profile(last_response):
                    return last_response, endpoint
                errors.append(last_response[:250])
            except PortalError as exc:
                errors.append(str(exc))

        # Some PNPSCADA installations do not share the browser session with the
        # web-service listener. POST credentials in the encrypted request body,
        # never in the URL or logs.
        post_params = dict(params)
        post_params.update({"LOGIN": self.username, "PWD": self._password})
        endpoint = self._profile_candidates()[-1]
        last_response = self._request(endpoint, post_params, method="POST")
        self._record_diagnostic(
            f"profile_{account.eid}_{urlparse(endpoint).port or 443}_post.xml",
            endpoint,
            last_response,
        )
        if self._is_success_profile(last_response):
            return last_response, endpoint
        errors.append(last_response[:250])
        clean_errors = " | ".join(item.replace(self._password, "[REDACTED]") for item in errors)
        raise ProfileAccessError(f"No profile data was returned for {account.name}: {clean_errors}")

    def fetch_daily_report(self, start_date: date, end_date_inclusive: date) -> tuple[str, str]:
        exclusive_end = end_date_inclusive + timedelta(days=1)
        params = {
            "d": "d",
            "sDate": start_date.strftime("%Y-%m-%d 00:00:00"),
            "eDate": exclusive_end.strftime("%Y-%m-%d 00:00:00"),
            "TGIDX": "0",
        }
        if self.memh:
            params["memh"] = self.memh
        endpoint = urljoin(self.base_url + "/", "perDayKWhShiftRep.jsp")
        text = self._request(endpoint + "?" + urlencode(params))
        self._record_diagnostic("per_day_report.html", endpoint, text)
        if _looks_like_auth_error(text):
            raise ProfileAccessError("The authenticated account cannot open the daily report")
        return text, endpoint

    def discover_graph_account_ids(self, accounts: list[MeterAccount]) -> dict[str, str]:
        """Map configured accounts to the internal IDs used by the Profile Graph UI."""

        if self.authenticated_start_html is None:
            raise AuthenticationError("Log in before discovering Profile Graph accounts")
        graph = parse_profile_graph(self.authenticated_start_html)
        if not graph.account_options:
            params = {"memh": self.memh or ""}
            html = self._request(
                urljoin(self.base_url + "/", "_Graph") + "?" + urlencode(params)
            )
            self.memh = _extract_memh(html) or self.memh
            graph = parse_profile_graph(html)
        if not graph.account_options:
            raise ProfileAccessError("The Profile Graph meter-account selector was not found")

        by_name = {" ".join(name.split()).casefold(): value for name, value in graph.account_options.items()}
        mapping: dict[str, str] = {}
        missing: list[str] = []
        for account in accounts:
            internal_id = by_name.get(" ".join(account.name.split()).casefold())
            if internal_id:
                mapping[account.eid] = internal_id
            else:
                missing.append(account.name)
        if missing:
            raise ProfileAccessError(
                "These configured accounts were not present in the Profile Graph selector: "
                + ", ".join(missing)
            )
        return mapping

    def fetch_profile_graph_csv(
        self,
        account: MeterAccount,
        internal_account_id: str,
        start_date: date,
        end_date_inclusive: date,
    ) -> tuple[str, str]:
        """Use the same Profile Graph -> Download CSV workflow exposed by the website."""

        if self.username is None:
            raise AuthenticationError("Log in before downloading Profile Graph data")
        graph_params = {
            "memh": self.memh or "",
            "PNPENTID": internal_account_id,
            "PNPENTCLASID": "109",
        }
        graph_url = urljoin(self.base_url + "/", "_Graph") + "?" + urlencode(graph_params)
        graph_html = self._request(graph_url)
        if _looks_like_login_page(graph_html) or _looks_like_auth_error(graph_html):
            raise AuthenticationError("PNPSCADA ended the session while opening Profile Graph")
        self.memh = _extract_memh(graph_html) or self.memh
        graph = parse_profile_graph(graph_html)
        if not graph.selected_utility:
            raise ProfileAccessError(f"Profile Graph did not select {account.name}")
        selected_eid = graph.selected_utility.split("$", 1)[0]
        if selected_eid != account.eid:
            raise ProfileAccessError(
                f"Profile Graph selected entity {selected_eid} instead of {account.eid} for {account.name}"
            )

        # PNPSCADA's CSV route excludes a sample exactly equal to GSTARTH.
        # Prefetch the prior day; the parser/service filters it back to the
        # requested inclusive dates. This preserves the requested day's 00:00
        # interval instead of silently producing a 23.5-hour first day.
        download_start = start_date - timedelta(days=1)
        exclusive_end = end_date_inclusive + timedelta(days=1)
        params = {
            "CSV": "Yes",
            "GSTARTH": "0",
            "GSTARTN": "0",
            "GENDH": "0",
            "GENDN": "0",
            "GSTARTD": str(download_start.day),
            "GSTARTY": str(download_start.year),
            "GSTARTM": str(download_start.month),
            "GENDD": str(exclusive_end.day),
            "GENDY": str(exclusive_end.year),
            "GENDM": str(exclusive_end.month),
            "TEMPPATH": "../temp/",
            "LOCALTEMPPATH": "docroot/temp/",
            "memh": self.memh or "",
            "selGNAME_UTILITY": graph.selected_utility,
            "TGIDX": "0",
        }
        endpoint = urljoin(self.base_url + "/", "_DataDownload")
        text = self._request(endpoint + "?" + urlencode(params))
        if _looks_like_login_page(text) or _looks_like_auth_error(text):
            raise AuthenticationError("PNPSCADA ended the session during the CSV download")
        header_probe = text[:2000].lower()
        if "date" not in header_probe or "time" not in header_probe or "per kw" not in header_probe:
            self._record_diagnostic(f"data_download_{account.eid}.txt", endpoint, text)
            raise ProfileAccessError(f"PNPSCADA did not return Profile Graph CSV data for {account.name}")
        return text, self.last_url or endpoint

    def capture_navigation_pages(self, accounts: list[MeterAccount], max_pages: int = 24) -> None:
        """Read-only crawl of the authenticated UI to locate account/profile links.

        It follows only same-host navigation and frames, and explicitly excludes
        logout/edit/delete/update/payment style actions.
        """

        if not self.authenticated_start_url or self.authenticated_start_html is None:
            return
        base_host = urlparse(self.base_url).netloc.lower()
        blocked = (
            "logout",
            "delete",
            "remove",
            "update",
            "save",
            "switchbreaker",
            "sendsts",
            "payment",
            "paynow",
            "_login",
        )
        account_terms = [
            term.lower()
            for account in accounts
            for term in (account.name, account.code, account.eid)
            if term
        ]
        queue: list[tuple[int, str, str | None]] = [
            (1000, self.authenticated_start_url, self.authenticated_start_html)
        ]
        visited: set[str] = set()
        page_number = 0
        while queue and page_number < max_pages:
            queue.sort(key=lambda item: item[0], reverse=True)
            _score, url, supplied_html = queue.pop(0)
            parsed_url = urlparse(url)
            normalized = urlunparse(
                (parsed_url.scheme, parsed_url.netloc, parsed_url.path, parsed_url.params, parsed_url.query, "")
            )
            if normalized in visited:
                continue
            visited.add(normalized)
            try:
                html = supplied_html if supplied_html is not None else self._request(normalized)
            except PortalError:
                continue
            page_number += 1
            self._record_diagnostic(f"ui_page_{page_number:02d}.html", normalized, html)
            parser = NavigationParser()
            parser.feed(html)
            targets: list[PageLink] = list(parser.links)
            targets.extend(PageLink(target, "frame or script") for target in parser.frames)
            targets.extend(PageLink(target, "script redirect") for target in _script_targets(html))
            for link in targets:
                href = link.href.strip()
                if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    continue
                absolute = urljoin(normalized, href)
                candidate = urlparse(absolute)
                if candidate.scheme != "https" or candidate.netloc.lower() != base_host:
                    continue
                searchable = f"{link.text} {candidate.path} {candidate.query}".lower()
                if any(word in searchable for word in blocked):
                    continue
                priority = 1
                if any(term in searchable for term in account_terms):
                    priority += 500
                if any(word in searchable for word in ("profile", "graph", "download", "csv")):
                    priority += 250
                if any(word in searchable for word in ("overview", "meter account", "meteraccount")):
                    priority += 100
                queue.append((priority, absolute, None))
