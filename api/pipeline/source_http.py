"""Source-bound requests: validate every URL before any redirected request."""
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from pipeline.source_registry import url_matches_source


def validate_source_url(source, url):
    if not url_matches_source(source, url) or urlsplit(url).port not in (None,443):
        raise ValueError('URL is outside registered source HTTPS policy.')


class SourceRedirects(HTTPRedirectHandler):
    def __init__(self, source):
        self.source = source
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_source_url(self.source, newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_source(source, url, timeout=20, max_response=4*1024*1024):
    validate_source_url(source, url)
    opener = build_opener(SourceRedirects(source))
    req = Request(url, headers={'User-Agent':'SkinSync/1.0 (+product-ingredient-research)'})
    with opener.open(req, timeout=timeout) as response:
        validate_source_url(source, response.geturl())
        payload = response.read(max_response+1)
        if len(payload)>max_response:
            raise ValueError('Source response exceeds bounded collection limit.')
        return payload.decode('utf-8','replace'), response.geturl()
