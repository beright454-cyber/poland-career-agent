import random
import re
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
import dns.resolver
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

BLOCKED_DOMAINS = {
    "google.com", "google.pl", "wikipedia.org", "youtube.com", "facebook.com",
    "linkedin.com", "instagram.com", "twitter.com", "x.com", "pracuj.pl",
    "justjoin.it", "nofluffjobs.com", "theprotocol.it", "bulldogjob.pl",
    "indeed.com", "olx.pl", "praca.pl", "gowork.pl", "glassdoor.com",
    "rocketjobs.pl", "useme.com", "oferia.pl", "praca.gov.pl", "webofscience.com",
    "allegro.pl", "ceneo.pl", "wikipedia.pl", "gumtree.pl", "szukampracy.pl",
    "aplikuj.pl", "infopraca.pl", "naukawpolsce.pl", "jooble.org", "pl.jooble.org"
}

SEARCH_QUERIES = [
    '"software house" "kontakt" Warszawa "@"',
    '"software house" "kariera" Łódź "@"',
    '"agencja interaktywna" "kontakt" Warszawa "@"',
    '"agencja interaktywna" "kontakt" Łódź "@"',
    '"tworzenie stron internetowych" "kontakt" Warszawa "@"',
    '"tworzenie stron internetowych" "kontakt" Łódź "@"',
    '"web development" "contact" Warsaw "@"',
    '"rekrutacja@" "developer" Warszawa',
    '"kariera@" "developer" Polska',
    '"praca@" "web" Warszawa',
    '"biuro@" "software house" Polska',
    '"kontakt@" "agencja interaktywna" Polska',
    '"e-commerce" "kariera" "kontakt@" Polska',
    '"automatyzacja" "python" "kontakt@" Polska',
    '"junior" "web developer" "kariera@" Polska',
]


class PolandHRFinder:
    def __init__(self):
        self.query_index = 0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8"})

    def _clean_domain(self, url: str) -> str:
        try:
            netloc = urlparse(url).netloc.lower().strip()
            if netloc.startswith("www."):
                netloc = netloc[4:]
            return netloc
        except Exception:
            return ""

    def _is_valid_company_domain(self, domain: str) -> bool:
        if not domain or "." not in domain:
            return False
        for blocked in BLOCKED_DOMAINS:
            if domain == blocked or domain.endswith("." + blocked):
                return False
        return True

    def _is_allowed_by_robots(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            rp = RobotFileParser()
            resp = self.session.get(robots_url, timeout=4)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
                return rp.can_fetch(USER_AGENT, url)
            return True
        except Exception:
            return True

    def _has_valid_mx(self, domain: str) -> bool:
        try:
            answers = dns.resolver.resolve(domain, "MX", lifetime=4.0)
            return len(answers) > 0
        except Exception:
            return False

    def _extract_same_domain_emails(self, html: str, soup: BeautifulSoup, domain: str) -> list[str]:
        candidates = set(re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", html))

        # Also inspect mailto: links explicitly
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().startswith("mailto:"):
                addr = href[7:].split("?")[0].strip()
                if "@" in addr:
                    candidates.add(addr)

        valid = []
        for em in candidates:
            em_low = em.lower().strip()
            if any(em_low.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js"]):
                continue
            em_dom = em_low.split("@")[-1]
            if em_dom == domain or em_dom.endswith("." + domain):
                valid.append(em_low)

        priority_prefixes = (
            "rekrutacja@", "kariera@", "hr@", "jobs@", "praca@", "talent@",
            "cv@", "biuro@", "kontakt@", "contact@", "office@", "hello@", "info@"
        )
        valid.sort(key=lambda e: next((i for i, p in enumerate(priority_prefixes) if e.startswith(p)), 99))
        return list(dict.fromkeys(valid))

    def search_hiring_companies(self, max_results: int = 25) -> list[dict]:
        candidates = []
        seen_domains = set()

        queries_this_round = []
        for _ in range(3):
            queries_this_round.append(SEARCH_QUERIES[self.query_index % len(SEARCH_QUERIES)])
            self.query_index += 1

        for query in queries_this_round:
            try:
                with DDGS() as ddgs:
                    results = list(ddgs.text(query, region="pl-pl", max_results=12))
                for r in results:
                    url = r.get("href") or r.get("link") or ""
                    title = r.get("title") or ""
                    domain = self._clean_domain(url)
                    if not self._is_valid_company_domain(domain):
                        continue
                    if domain in seen_domains:
                        continue
                    seen_domains.add(domain)
                    company_name = domain.split(".")[0].replace("-", " ").title()
                    candidates.append({
                        "company_name": company_name,
                        "company_domain": domain,
                        "job_url": url,
                        "page_title": title,
                    })
                    if len(candidates) >= max_results:
                        break
                time.sleep(2.0)
            except Exception:
                time.sleep(3.0)
                continue

        random.shuffle(candidates)
        return candidates[:max_results]

    def inspect_company_and_hr(self, url: str, domain: str) -> dict:
        if not self._is_allowed_by_robots(url):
            return {"ok": False, "reason": "Blocked by robots.txt"}

        if not self._has_valid_mx(domain):
            return {"ok": False, "reason": "No active MX mail server"}

        parsed = urlparse(url)
        base_root = f"{parsed.scheme}://{parsed.netloc}"
        pages_to_check = [
            url,
            urljoin(base_root, "/kariera"),
            urljoin(base_root, "/praca"),
            urljoin(base_root, "/kontakt"),
            urljoin(base_root, "/contact"),
            urljoin(base_root, "/careers"),
            base_root,
        ]
        unique_pages = list(dict.fromkeys(pages_to_check))

        combined_text = ""
        found_emails = []

        for page_url in unique_pages:
            try:
                resp = self.session.get(page_url, timeout=7)
                if resp.status_code != 200:
                    continue
                html = resp.text
                soup = BeautifulSoup(html, "html.parser")
                emails = self._extract_same_domain_emails(html, soup, domain)

                for tag in soup(["script", "style", "noscript"]):
                    tag.decompose()
                page_text = " ".join(soup.get_text(" ", strip=True).split())
                if len(combined_text) < 2500:
                    combined_text += " " + page_text[:900]

                if emails:
                    found_emails = emails
                    break
                time.sleep(1.0)
            except Exception:
                continue

        if not found_emails:
            return {"ok": False, "reason": "No same-domain HR/Contact email on site"}

        return {
            "ok": True,
            "hr_email": found_emails[0],
            "page_text": combined_text.strip()[:2200],
        }


poland_hr_finder = PolandHRFinder()