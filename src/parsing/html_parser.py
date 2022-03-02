from __future__ import annotations
from collections.abc import Iterator, Iterable
from typing import Union, Optional

import re
import minify_html
from bs4 import BeautifulSoup
from bs4.element import NavigableString, PageElement, Tag
from urllib.parse import urlparse, urljoin
from attr import define

from src import web
from .medium import Video, Image, Media, Animation
from .html_node import *
from .utils import stripNewline, stripLineEnd, is_absolute_link, emojify

isSmallIcon = re.compile(r'(width|height): ?(([012]?\d|30)(\.\d)?px|([01](\.\d)?|2)r?em)').search
srcsetParser = re.compile(r'(?:^|,\s*)'
                          r'(?P<url>\S+)'  # allow comma here because it is valid in URL
                          r'(?:\s+'
                          r'(?P<number>\d+(\.\d+)?)'
                          r'(?P<unit>[wx])'
                          r')?'
                          r'\s*'
                          r'(?=,|$)').finditer  # e.g.: url,url 1x,url 2x,url 100w,url 200w


class Parser:
    def __init__(self, html: str, feed_link: Optional[str] = None):
        """
        :param html: HTML content
        :param feed_link: feed link (use for resolve relative urls)
        """
        self.html = minify_html.minify(html,
                                       do_not_minify_doctype=True,
                                       keep_closing_tags=True,
                                       keep_spaces_between_attributes=True,
                                       ensure_spec_compliant_unquoted_attribute_values=True,
                                       remove_processing_instructions=True)
        self.soup = BeautifulSoup(self.html, 'lxml')
        self.media: Media = Media()
        self.html_tree = HtmlTree('')
        self.feed_link = feed_link
        self.parsed = False

    async def parse(self):
        self.html_tree = HtmlTree(await self._parse_item(self.soup))
        self.parsed = True

    def get_parsed_html(self):
        if not self.parsed:
            raise RuntimeError('You must parse the HTML first')
        return stripNewline(stripLineEnd(self.html_tree.get_html().strip()))

    async def _parse_item(self, soup: Union[PageElement, BeautifulSoup, Tag, NavigableString, Iterable[PageElement]]):
        result = []
        if isinstance(soup, Iterator):  # a Tag is also Iterable, but we only expect an Iterator here
            for child in soup:
                item = await self._parse_item(child)
                if item:
                    result.append(item)
            if not result:
                return None
            return result[0] if len(result) == 1 else Text(result)

        if isinstance(soup, NavigableString):
            if type(soup) is NavigableString:
                return Text(emojify(str(soup)))
            return None  # we do not expect a subclass of NavigableString here, drop it

        if not isinstance(soup, Tag):
            return None

        tag = soup.name
        if tag is None:
            return None

        if tag == 'p' or tag == 'section':
            parent = soup.parent.name
            text = await self._parse_item(soup.children)
            if text:
                return Text([Br(), text, Br()]) if parent != 'li' else text
            else:
                return None

        if tag == 'blockquote':
            quote = await self._parse_item(soup.children)
            if not quote:
                return None
            quote.strip()
            return Text([Hr(), quote, Hr()])

        if tag == 'pre':
            return Pre(await self._parse_item(soup.children))

        if tag == 'code':
            return Code(await self._parse_item(soup.children))

        if tag == 'br':
            return Br()

        if tag == 'a':
            text = await self._parse_item(soup.children)
            if not text:
                return None
            href = soup.get("href")
            if not href:
                return None
            if not is_absolute_link(href) and self.feed_link:
                href = urljoin(self.feed_link, href)
            return Link(await self._parse_item(soup.children), href)

        if tag == 'img':
            src, srcset = soup.get('src'), soup.get('srcset')
            if not (src or srcset):
                return None
            alt, _class = soup.get('alt', ''), soup.get('class', '')
            style, width, height = soup.get('style', ''), soup.get('width', ''), soup.get('height', '')
            width = int(width) if width and width.isdigit() else float('inf')
            height = int(height) if height and height.isdigit() else float('inf')
            if width <= 30 or height <= 30 or isSmallIcon(style) \
                    or 'emoji' in _class or (alt.startswith(':') and alt.endswith(':')):
                return Text(emojify(alt)) if alt else None
            _multi_src = []
            if srcset:
                srcset_matches: list[dict[str, Union[int, str]]] = [{
                    'url': match['url'],
                    'number': float(match['number']) if match['number'] else 1,
                    'unit': match['unit'] if match['unit'] else 'x'
                } for match in (
                    match.groupdict() for match in srcsetParser(srcset)
                )] + ([{'url': src, 'number': 1, 'unit': 'x'}] if src else [])
                if srcset_matches:
                    srcset_matches_unit_w = [match for match in srcset_matches if match['unit'] == 'w']
                    srcset_matches_unit_x = [match for match in srcset_matches if match['unit'] == 'x']
                    srcset_matches_unit_w.sort(key=lambda match: float(match['number']), reverse=True)
                    srcset_matches_unit_x.sort(key=lambda match: float(match['number']), reverse=True)
                    while True:
                        src_match_unit_w = srcset_matches_unit_w.pop(0) if srcset_matches_unit_w else None
                        src_match_unit_x = srcset_matches_unit_x.pop(0) if srcset_matches_unit_x else None
                        if not (src_match_unit_w or src_match_unit_x):
                            break
                        if src_match_unit_w:
                            _multi_src.append(src_match_unit_w['url'])
                        if src_match_unit_x:
                            if float(src_match_unit_x['number']) <= 1 and srcset_matches_unit_w:
                                srcset_matches_unit_x.insert(0, src_match_unit_x)
                                continue  # let src using unit w win
                            _multi_src.append(src_match_unit_x['url'])
            else:
                _multi_src.append(src) if src else None
            multi_src = []
            is_gif = False
            for _src in _multi_src:
                if not isinstance(_src, str):
                    continue
                if not is_absolute_link(_src) and self.feed_link:
                    _src = urljoin(self.feed_link, _src)
                if urlparse(_src).path.endswith(('.gif', '.gifv', '.webm', '.mp4', '.m4v')):
                    is_gif = True
                multi_src.append(_src)
            if multi_src:
                self.media.add(Image(multi_src) if not is_gif else Animation(multi_src))
            return None

        if tag == 'video':
            src = soup.get('src')
            poster = soup.get('poster')
            _multi_src = [t['src'] for t in soup.find_all(name='source') if t.get('src')]
            if src:
                _multi_src.append(src)
            multi_src = []
            for _src in _multi_src:
                if not isinstance(_src, str):
                    continue
                if not is_absolute_link(_src) and self.feed_link:
                    _src = urljoin(self.feed_link, _src)
                multi_src.append(_src)
            if multi_src:
                self.media.add(Video(multi_src, type_fallback_urls=poster))
            return None

        if tag == 'b' or tag == 'strong':
            text = await self._parse_item(soup.children)
            return Bold(text) if text else None

        if tag == 'i' or tag == 'em':
            text = await self._parse_item(soup.children)
            return Italic(text) if text else None

        if tag == 'u' or tag == 'ins':
            text = await self._parse_item(soup.children)
            return Underline(text) if text else None

        if tag == 'h1':
            text = await self._parse_item(soup.children)
            return Text([Br(2), Bold(Underline(text)), Br()]) if text else None

        if tag == 'h2':
            text = await self._parse_item(soup.children)
            return Text([Br(2), Bold(text), Br()]) if text else None

        if tag == 'hr':
            return Hr()

        if tag.startswith('h') and len(tag) == 2:
            text = await self._parse_item(soup.children)
            return Text([Br(2), Underline(text), Br()]) if text else None

        if tag == 'li':
            text = await self._parse_item(soup.children)
            return ListItem(text) if text else None

        if tag == 'iframe':
            text = await self._parse_item(soup.children)
            src = soup.get('src')
            if not src:
                return None
            if not is_absolute_link(src) and self.feed_link:
                src = urljoin(self.feed_link, src)
            if not text:
                # noinspection PyBroadException
                try:
                    page = await web.get(src, timeout=3, decode=True, semaphore=False)
                    if page.status != 200:
                        raise ValueError
                    text = BeautifulSoup(page.content, 'lxml').title.text
                except Exception:
                    pass
                finally:
                    if not text:
                        text = urlparse(src).netloc
            return Text([Br(2), Link(f'iframe ({text})', param=src), Br(2)])

        in_list = tag == 'ol' or tag == 'ul'
        for child in soup.children:
            item = await self._parse_item(child)
            if item and (not in_list or type(child) is not NavigableString):
                result.append(item)
        if tag == 'ol':
            return OrderedList([Br(), *result, Br()])
        elif tag == 'ul':
            return UnorderedList([Br(), *result, Br()])
        else:
            return result[0] if len(result) == 1 else Text(result)

    def __repr__(self):
        return repr(self.html_tree)

    def __str__(self):
        return str(self.html_tree)


@define
class Parsed:
    html_tree: HtmlTree
    media: Media
    html: str


async def parse(html: str, feed_link: Optional[str] = None):
    """
    :param html: HTML content
    :param feed_link: feed link (use for resolve relative urls)
    """
    parser = Parser(html=html, feed_link=feed_link)
    await parser.parse()
    return Parsed(html_tree=parser.html_tree, media=parser.media, html=parser.get_parsed_html())
