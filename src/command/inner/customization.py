from __future__ import annotations
from typing import Union, Optional
from collections.abc import Iterable

from itertools import chain
from telethon import Button
from telethon.tl.types import KeyboardButtonCallback

from src import db, env
from src.i18n import i18n
from .utils import arrange_grid, update_interval, activate_or_deactivate_sub, formatting_time, logger, \
    construct_hashtags

SUB_OPTIONS_EXHAUSTIVE_VALUES = {
    "notify": (1, 0),
    "send_mode": (0, 1, 2, -1),
    "link_preview": (0, 1),
    "display_media": (0, -1),
    "display_author": (0, 1, -1),
    # "display_via": (-2, -1, 0, 1),  # currently 1 will be treated as 0
    "display_via": (0, -1, -2),
    "display_title": (0, 1, -1),
    "style": (0, 1)
}

FALLBACK_TO_USER_DEFAULT_EMOJI = "↩️"


async def get_sub_info(sub: db.Sub,
                       lang: Optional[str] = None,
                       including_default_prompt: bool = False) -> str:
    if not isinstance(sub.feed, db.Feed):
        await sub.fetch_related('feed')
    info = (
            f"<b>{i18n[lang]['subscription_info']}</b>\n\n"
            f"{i18n[lang]['feed_title']}: {sub.feed.title}\n"
            f"{i18n[lang]['feed_url']}: {sub.feed.link}\n"
            + (f"\n{i18n[lang]['subscription_title']}: {sub.title}" if sub.title else '')
            + (f"\n{i18n[lang]['hashtags']}: {construct_hashtags(sub.tags)}" if sub.tags else '')
            + (f"\n\n{i18n[lang]['default_emoji_header_description'] % (FALLBACK_TO_USER_DEFAULT_EMOJI,)}"
               if including_default_prompt else '')
    )
    return info


# noinspection DuplicatedCode
async def get_customization_buttons(sub_or_user: Union[db.Sub, db.User],
                                    lang: Optional[str] = None,
                                    page: Optional[int] = None) -> tuple[tuple[KeyboardButtonCallback, ...], ...]:
    page = page or 1
    is_user = isinstance(sub_or_user, db.User)
    if is_user:
        interval_d = length_limit_d = notify_d = send_mode_d = link_preview_d = display_media_d = display_author_d = \
            display_via_d = display_title_d = style_d = False
        all_default = None
    else:
        if not isinstance(sub_or_user.user, db.User):
            await sub_or_user.fetch_related('user')
        interval_d = sub_or_user.interval is None
        length_limit_d = sub_or_user.length_limit == -100
        notify_d = sub_or_user.notify == -100
        send_mode_d = sub_or_user.send_mode == -100
        link_preview_d = sub_or_user.link_preview == -100
        display_media_d = sub_or_user.display_media == -100
        display_author_d = sub_or_user.display_author == -100
        display_via_d = sub_or_user.display_via == -100
        display_title_d = sub_or_user.display_title == -100
        style_d = sub_or_user.style == -100
        all_default = all((interval_d, length_limit_d, notify_d, send_mode_d, link_preview_d, display_media_d,
                           display_author_d, display_via_d, display_title_d, style_d))
    interval = sub_or_user.interval if not interval_d else sub_or_user.user.interval
    length_limit = sub_or_user.length_limit if not length_limit_d else sub_or_user.user.length_limit
    notify = sub_or_user.notify if not notify_d else sub_or_user.user.notify
    send_mode = sub_or_user.send_mode if not send_mode_d else sub_or_user.user.send_mode
    link_preview = sub_or_user.link_preview if not link_preview_d else sub_or_user.user.link_preview
    display_media = sub_or_user.display_media if not display_media_d else sub_or_user.user.display_media
    display_author = sub_or_user.display_author if not display_author_d else sub_or_user.user.display_author
    display_via = sub_or_user.display_via if not display_via_d else sub_or_user.user.display_via
    display_title = sub_or_user.display_title if not display_title_d else sub_or_user.user.display_title
    style = sub_or_user.style if not style_d else sub_or_user.user.style
    buttons = (
        (
            Button.inline(f"{i18n[lang]['status']}: "
                          + i18n[lang]['status_activated' if sub_or_user.state == 1 else 'status_deactivated'],
                          data=f'set={sub_or_user.id},activate|{page}') 
            if not is_user else
            Button.inline(FALLBACK_TO_USER_DEFAULT_EMOJI + i18n[lang]['reset_all_button'],
                          data=f'reset_all_confirm'),
        ),
        (
            Button.inline(FALLBACK_TO_USER_DEFAULT_EMOJI + i18n[lang]['use_user_default_button'],
                          data=f'reset={sub_or_user.id}|{page}'),
        ) if not is_user and not all_default else None,
        (
            Button.inline(f"{i18n[lang]['monitor_interval']}: "
                          + (FALLBACK_TO_USER_DEFAULT_EMOJI if interval_d else '')
                          + formatting_time(minutes=interval or db.EffectiveOptions.default_interval),
                          data=(f'set={sub_or_user.id},interval|{page}'
                                if not is_user
                                else f'set_default=interval')),
        ),
        (
            Button.inline(f"{i18n[lang]['notification']}: "
                          + (FALLBACK_TO_USER_DEFAULT_EMOJI if notify_d else '')
                          + i18n[lang]['notification_normal' if notify else 'notification_muted'],
                          data=(f'set={sub_or_user.id},notify|{page}'
                                if not is_user
                                else f'set_default=notify')),
        ),
        (
            Button.inline(f"{i18n[lang]['send_mode']}: "
                          + (FALLBACK_TO_USER_DEFAULT_EMOJI if send_mode_d else '')
                          + i18n[lang][f'send_mode_{send_mode}'],
                          data=(f'set={sub_or_user.id},send_mode|{page}'
                                if not is_user
                                else f'set_default=send_mode')),
        ),
        (
            Button.inline(f"{i18n[lang]['length_limit']}: "
                          + (FALLBACK_TO_USER_DEFAULT_EMOJI if length_limit_d else '')
                          + (str(sub_or_user.length_limit) if length_limit else i18n[lang]['length_limit_unlimited']),
                          data=(f'set={sub_or_user.id},length_limit|{page}'
                                if not is_user
                                else f'set_default=length_limit')),
        ),
        (
            Button.inline(f"{i18n[lang]['display_media']}: "
                          + (FALLBACK_TO_USER_DEFAULT_EMOJI if display_media_d else '')
                          + i18n[lang][f'display_media_{display_media}'],
                          data=(f'set={sub_or_user.id},display_media|{page}'
                                if not is_user
                                else f'set_default=display_media')),
        ),
        (
            Button.inline(f"{i18n[lang]['display_title']}: "
                          + (FALLBACK_TO_USER_DEFAULT_EMOJI if display_title_d else '')
                          + i18n[lang][f'display_title_{display_title}'],
                          data=(f'set={sub_or_user.id},display_title|{page}'
                                if not is_user
                                else f'set_default=display_title')),
        ),
        (
            Button.inline(f"{i18n[lang]['display_via']}: "
                          + (FALLBACK_TO_USER_DEFAULT_EMOJI if display_via_d else '')
                          + i18n[lang][f'display_via_{display_via}'],
                          data=(f'set={sub_or_user.id},display_via|{page}'
                                if not is_user
                                else f'set_default=display_via')),
        ),
        (
            Button.inline(f"{i18n[lang]['display_author']}: "
                          + (FALLBACK_TO_USER_DEFAULT_EMOJI if display_author_d else '')
                          + i18n[lang][f'display_author_{display_author}'],
                          data=(f'set={sub_or_user.id},display_author|{page}'
                                if not is_user
                                else f'set_default=display_author')),
        ),
        (
            Button.inline(f"{i18n[lang]['link_preview']}: "
                          + (FALLBACK_TO_USER_DEFAULT_EMOJI if link_preview_d else '')
                          + i18n[lang][f'link_preview_{link_preview}'],
                          data=(f'set={sub_or_user.id},link_preview|{page}'
                                if not is_user else
                                f'set_default=link_preview')),
            Button.inline(f"{i18n[lang]['style']}: "
                          + (FALLBACK_TO_USER_DEFAULT_EMOJI if style_d else '')
                          + i18n[lang][f'style_{style}'],
                          data=(f'set={sub_or_user.id},style|{page}'
                                if not is_user
                                else f'set_default=style')),
        ),
        (
            Button.switch_inline(f"{i18n[lang]['set_custom_title_button']}",
                                 query=f'/set_title {sub_or_user.id} ',
                                 same_peer=True),
            Button.switch_inline(f"{i18n[lang]['set_custom_hashtags_button']}",
                                 query=f'/set_hashtags {sub_or_user.id} ',
                                 same_peer=True),
        ) if not is_user else None,
        (
            Button.inline(f'< {i18n[lang]["back"]}', data=f'get_set_page|{page}'),
        ) if not is_user else
        (
            Button.inline(f'{i18n[lang]["cancel"]}', data=f'cancel'),
        ),
    )
    return tuple(filter(None, buttons))


async def get_set_interval_buttons(sub_or_user: Union[db.Sub, int],
                                   lang: Optional[str] = None,
                                   page: Optional[int] = None) -> tuple[tuple[KeyboardButtonCallback, ...], ...]:
    is_user = isinstance(sub_or_user, db.User)
    page = page or 1

    minimal_interval: int = db.EffectiveOptions.minimal_interval

    if (sub_or_user.user_id if not is_user else sub_or_user.id) == env.MANAGER:
        minimal_interval = min(minimal_interval, 1)

    columns = 4
    buttons_in_minute_and_hour_count = sum(
        1 for interval in chain(
            range(1, 5),
            range(5, 61, 5),
            range(2 * 60, 24 * 60, 60)
        ) if interval >= minimal_interval
    )
    buttons_in_day_count = columns - buttons_in_minute_and_hour_count % columns

    buttons = (
            ((
                 Button.inline(FALLBACK_TO_USER_DEFAULT_EMOJI + i18n[lang][f'use_user_default_button'],
                               data=f'set={sub_or_user.id},interval,default|{page}'),
             ) if not is_user else None,)
            +
            arrange_grid(
                to_arrange=chain(
                    (
                        Button.inline('1h' if interval == 60 else f'{interval}min',
                                      data=f'set={sub_or_user.id},interval,{interval}|{page}'
                                      if not is_user else
                                      f'set_default=interval,{interval}')
                        for interval in chain(range(1, 5), range(5, 61, 5)) if interval >= minimal_interval
                    ),
                    (
                        Button.inline(f'{interval}h',
                                      data=f'set={sub_or_user.id},interval,{interval * 60}|{page}'
                                      if not is_user else
                                      f'set_default=interval,{interval * 60}')
                        for interval in range(2, 24) if interval * 60 >= minimal_interval
                    ),
                    (
                        Button.inline(f'{interval}d',
                                      data=f'set={sub_or_user.id},interval,{interval * 60 * 24}|{page}'
                                      if not is_user else
                                      f'set_default=interval,{interval * 60 * 24}')
                        for interval in range(1, buttons_in_day_count + 1) if interval * 60 * 24 >= minimal_interval
                    )
                ),
                columns=columns
            )
            +
            ((
                 Button.switch_inline(f"{i18n[lang]['set_custom_interval_button']}",
                                      query=f'/set_interval {sub_or_user.id} '
                                      if not is_user else
                                      f'/set_interval default ',
                                      same_peer=True),
             ),)
            +
            ((
                 Button.inline(f'< {i18n[lang]["back"]}',
                               data=f'set={sub_or_user.id}|{page}'
                               if not is_user else
                               f'set_default'),
             ),)
    )
    return tuple(filter(None, buttons))


async def get_set_length_limit_buttons(sub_or_user: Union[db.Sub, db.User],
                                       lang: Optional[str] = None,
                                       page: Optional[int] = None) -> tuple[tuple[KeyboardButtonCallback, ...], ...]:
    is_user = isinstance(sub_or_user, db.User)
    page = page or 1

    length_limit_range = list(range(256, 4096 + 1, 256))

    buttons = (
            ((
                 Button.inline(FALLBACK_TO_USER_DEFAULT_EMOJI + i18n[lang][f'use_user_default_button'],
                               data=f'set={sub_or_user.id},length_limit,default|{page}'),
             ) if not is_user else None,)
            +
            ((
                 Button.inline(i18n[lang]['length_limit_unlimited'],
                               data=f'set={sub_or_user.id},length_limit,0|{page}'
                               if not is_user else
                               f'set_default=length_limit,0'),
             ),)
            +
            arrange_grid(
                to_arrange=(
                    Button.inline(str(length_limit),
                                  data=f'set={sub_or_user.id},length_limit,{length_limit}|{page}'
                                  if not is_user else
                                  f'set_default=length_limit,{length_limit}')
                    for length_limit in length_limit_range
                ),
                columns=4
            )
            +
            ((
                 Button.inline(f'< {i18n[lang]["back"]}',
                               data=f'set={sub_or_user.id}|{page}'
                               if not is_user else
                               f'set_default'),
             ),)
    )
    return tuple(filter(None, buttons))


async def set_interval(sub_or_user: Union[db.Sub, db.User], interval: int) -> Union[db.Sub, db.User]:
    is_user = isinstance(sub_or_user, db.User)

    minimal_interval = db.EffectiveOptions.minimal_interval
    if not isinstance(interval, int) or interval <= 0:
        interval = None
    if interval and interval < minimal_interval and (sub_or_user.id if is_user else sub_or_user.user_id) != env.MANAGER:
        interval = minimal_interval
    if interval == sub_or_user.interval:
        return sub_or_user

    sub_or_user.interval = interval
    await sub_or_user.save()

    if is_user:
        subs = await db.Sub.filter(user_id=sub_or_user.id, interval__isnull=True)
        for sub in subs:
            env.loop.create_task(update_interval(sub))
    else:
        await update_interval(sub_or_user)

    return sub_or_user


async def set_length_limit(sub_or_user: Union[db.Sub, db.User], length_limit: int) -> Union[db.Sub, db.User]:
    if length_limit == sub_or_user.length_limit:
        return sub_or_user

    if not 0 <= length_limit <= 4096:
        length_limit = -100 if isinstance(sub_or_user, db.Sub) else 0

    sub_or_user.length_limit = length_limit
    await sub_or_user.save()
    return sub_or_user


async def set_sub_activate(sub: db.Sub) -> db.Sub:
    activated = sub.state == 1
    await activate_or_deactivate_sub(sub.user_id, sub, activate=not activated)

    return sub


async def set_exhaustive_option(sub_or_user: Union[db.Sub, db.User], option: str) -> Union[db.Sub, db.User]:
    if option not in SUB_OPTIONS_EXHAUSTIVE_VALUES:
        raise KeyError(f'Invalid option: {option}')
    valid_values: tuple[int, ...] = SUB_OPTIONS_EXHAUSTIVE_VALUES[option]

    if isinstance(sub_or_user, db.Sub):
        valid_values = (-100,) + valid_values

    old_value = sub_or_user.__getattribute__(option)
    if old_value not in valid_values:
        old_value = valid_values[0]
    index = valid_values.index(old_value)
    new_value = valid_values[(index + 1) % len(valid_values)]
    sub_or_user.__setattr__(option, new_value)

    await sub_or_user.save()
    return sub_or_user


async def set_sub_title(sub: db.Sub, title: Optional[str]) -> db.Sub:
    if sub.title == title:
        return sub
    sub.title = title
    await sub.save()
    logger.info(f'Subscription {sub.id} of {sub.user_id} title changed to {title}')
    return sub


async def del_subs_title(subs: Union[Iterable[db.Sub], db.Sub]) -> int:
    if isinstance(subs, db.Sub):
        subs = (subs,)
    for sub in subs:
        sub.title = None
    return await db.Sub.bulk_update(subs, ['title'])


async def set_sub_hashtags(sub: db.Sub, hashtags: Union[Iterable[str], str, None]) -> db.Sub:
    if hashtags is None or isinstance(hashtags, str):
        hashtags_str = hashtags
    else:
        filtered_hashtags = []
        for hashtag in hashtags:
            hashtag = hashtag.strip(' \n\r\t#')
            if hashtag:
                filtered_hashtags.append(hashtag)
        hashtags_str = ' '.join(filtered_hashtags) if filtered_hashtags else None
    if sub.tags == hashtags_str:
        return sub
    sub.tags = hashtags_str
    await sub.save()
