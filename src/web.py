import asyncio
import functools
import os
import aiohttp
import aiohttp.client_exceptions
import feedparser
from concurrent.futures import ThreadPoolExecutor
from aiohttp_socks import ProxyConnector
from aiohttp_retry import RetryClient, ExponentialRetry
from typing import Union, Optional, Mapping, Dict
from ssl import SSLError
from ipaddress import ip_network, ip_address
from urllib.parse import urlparse
from collections import OrderedDict
from aiodns import DNSResolver
from socket import AF_INET6

from src import env, log
from src.i18n import i18n

if os.name == "nt":  # workaround for aiodns on Windows
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logger = log.getLogger('RSStT.web')

_feedparser_thread_pool = ThreadPoolExecutor(1, 'feedparser_')
_semaphore = asyncio.BoundedSemaphore(5)
_resolver = DNSResolver()

PROXY = env.R_PROXY.replace('socks5h', 'socks5').replace('sock4a', 'socks4') if env.R_PROXY else None
PRIVATE_NETWORKS = tuple(ip_network(ip_block) for ip_block in
                         ('127.0.0.0/8', '::1/128',  # loopback is not a private network, list in here for convenience
                          '169.254.0.0/16', 'fe80::/10',  # link-local address
                          '10.0.0.0/8',  # class A private network
                          '172.16.0.0/12',  # class B private networks
                          '192.168.0.0/16',  # class C private networks
                          'fc00::/7',  # ULA
                          ))

HEADER_TEMPLATE = OrderedDict({
    'Host': None,  # to be filled
    'User-Agent': env.USER_AGENT,
    'Accept': '*/*',
    'Accept-Encoding': 'gzip, deflate, br',
})
FEED_ACCEPT = 'application/rss+xml, application/rdf+xml, application/atom+xml, ' \
              'application/xml;q=0.9, text/xml;q=0.8, text/*;q=0.7, application/*;q=0.6'

RETRY_OPTION = ExponentialRetry(attempts=3, start_timeout=1,
                                exceptions={asyncio.exceptions.TimeoutError,
                                            aiohttp.client_exceptions.ClientError,
                                            ConnectionError,
                                            TimeoutError})


def proxy_filter(url: str) -> bool:
    if not (env.PROXY_BYPASS_PRIVATE or env.PROXY_BYPASS_DOMAINS):
        return True

    hostname = urlparse(url).hostname
    if env.PROXY_BYPASS_PRIVATE:
        try:
            ip_a = ip_address(hostname)
            is_private = any(ip_a in network for network in PRIVATE_NETWORKS)
            if is_private:
                return False
        except ValueError:
            pass  # not an IP, continue
    if env.PROXY_BYPASS_DOMAINS:
        is_bypassed = any(hostname.endswith(domain) and hostname[-len(domain) - 1] == '.'
                          for domain in env.PROXY_BYPASS_DOMAINS)
        if is_bypassed:
            return False
    return True


async def get(url: str, timeout: int = None, semaphore: Union[bool, asyncio.Semaphore] = None,
              headers: Optional[dict] = None, decode: bool = False, no_body: bool = False) \
        -> Dict[str, Union[Mapping[str, str], bytes, str, int]]:
    if not timeout:
        timeout = 12

    host = urlparse(url).hostname
    v6_address = None
    try:
        v6_address = await _resolver.query(host, 'AAAA') if env.IPV6_PRIOR else None
    except Exception:
        pass
    socket_family = AF_INET6 if v6_address else 0

    _headers = HEADER_TEMPLATE.copy()
    if headers:
        _headers.update(headers)
    _headers['Host'] = host

    proxy_connector = ProxyConnector.from_url(PROXY, family=socket_family) if (PROXY and proxy_filter(url)) \
        else aiohttp.TCPConnector(family=socket_family)

    await _semaphore.acquire() if semaphore is None or semaphore is True else \
        await semaphore.acquire() if semaphore else None

    try:
        async with RetryClient(retry_options=RETRY_OPTION, connector=proxy_connector,
                               timeout=aiohttp.ClientTimeout(total=timeout), headers=_headers) as session:
            async with session.get(url) as response:
                status = response.status
                content = (await (response.text() if decode else response.read())
                           if status == 200 and not no_body
                           else None)
                return {'url': str(response.url),  # get the redirected url
                        'content': content,
                        'headers': response.headers,
                        'status': status}
    finally:
        _semaphore.release() if semaphore is None or semaphore is True else \
            semaphore.release() if semaphore else None


async def get_session(timeout: int = None):
    if not timeout:
        timeout = 12

    proxy_connector = ProxyConnector.from_url(PROXY) if PROXY else None

    session = RetryClient(retry_options=RETRY_OPTION, connector=proxy_connector,
                          timeout=aiohttp.ClientTimeout(total=timeout), headers={'User-Agent': env.USER_AGENT})

    return session


async def feed_get(url: str, timeout: Optional[int] = None, web_semaphore: Union[bool, asyncio.Semaphore] = None,
                   headers: Optional[dict] = None, lang: Optional[str] = None, verbose: bool = True) \
        -> Dict[str, Union[Mapping[str, str], feedparser.FeedParserDict, str, int, None]]:
    auto_warning = logger.warning if verbose else logger.debug
    ret = {'url': url,
           'rss_d': None,
           'headers': None,
           'status': -1,
           'msg': None}
    _headers = {}
    if headers:
        _headers.update(headers)
    if 'Accept' not in _headers:
        _headers['Accept'] = FEED_ACCEPT

    try:
        _ = await get(url, timeout, web_semaphore, headers=_headers)
        rss_content = _['content']
        ret['url'] = _['url']
        ret['headers'] = _['headers']
        ret['status'] = _['status']

        # some rss feed implement http caching improperly :(
        if ret['status'] == 200 and int(ret['headers'].get('Content-Length', 1)) == 0:
            ret['status'] = 304
            ret['msg'] = f'"Content-Length" is 0'
            return ret

        if ret['status'] == 304:
            ret['msg'] = f'304 Not Modified'
            return ret  # 304 Not Modified, feed not updated

        if rss_content is None:
            auto_warning(f'Fetch failed (status code error, {ret["status"]}): {url}')
            ret['msg'] = f'ERROR: {i18n[lang]["status_code_error"]} ({_["status"]})'
            return ret

        if len(rss_content) <= 524288:
            rss_d = feedparser.parse(rss_content, sanitize_html=False)
        else:  # feed too large, run in another thread to avoid blocking the bot
            rss_d = await asyncio.get_event_loop().run_in_executor(_feedparser_thread_pool,
                                                                   functools.partial(feedparser.parse,
                                                                                     rss_content,
                                                                                     sanitize_html=False))

        if 'title' not in rss_d.feed:
            auto_warning(f'Fetch failed (feed invalid): {url}')
            ret['msg'] = 'ERROR: ' + i18n[lang]['feed_invalid']
            return ret

        ret['rss_d'] = rss_d
    except aiohttp.client_exceptions.InvalidURL:
        auto_warning(f'Fetch failed (URL invalid): {url}')
        ret['msg'] = 'ERROR: ' + i18n[lang]['url_invalid']
    except (asyncio.exceptions.TimeoutError,
            aiohttp.client_exceptions.ClientError,
            SSLError,
            OSError,
            ConnectionError,
            TimeoutError) as e:
        err_name = e.__class__.__name__
        auto_warning(f'Fetch failed (network error, {err_name}): {url}')
        ret['msg'] = f'ERROR: {i18n[lang]["network_error"]} ({err_name})'
    except Exception as e:
        auto_warning(f'Fetch failed: {url}', exc_info=e)
        ret['msg'] = 'ERROR: ' + i18n[lang]['internal_error']
    return ret
