from typing import Union, Optional
from telethon import events, Button
from telethon.tl.patched import Message

from . import inner, misc
from .utils import command_gatekeeper, parse_customization_callback_data, parse_callback_data_with_page, \
    escape_html, parse_command_get_sub_or_user_and_param
from src import db, env
from src.i18n import i18n


@command_gatekeeper(only_manager=False)
async def cmd_set_or_callback_get_set_page(event: Union[events.NewMessage.Event, Message, events.CallbackQuery.Event],
                                           *_,
                                           lang: Optional[str] = None,
                                           page: Optional[int] = None,
                                           **__):  # command: /set ; callback data: get_set_page|{page_number}
    is_callback = isinstance(event, events.CallbackQuery.Event)
    user_id = event.chat_id
    if not page:
        _, page = parse_callback_data_with_page(event.data) if is_callback else (None, 1)

    have_subs = await inner.utils.have_subs(event.chat_id)
    if not have_subs:
        msg = i18n[lang]['no_subscription']
        buttons = None
    else:
        msg = i18n[lang]['set_choose_sub_prompt']
        buttons = await inner.utils.get_sub_choosing_buttons(user_id, page_number=page, lang=lang,
                                                             callback='set',
                                                             get_page_callback='get_set_page')

    await event.respond(msg, buttons=buttons) if not is_callback else \
        await event.edit(msg, buttons=buttons)


@command_gatekeeper(only_manager=False)
async def callback_set(event: events.CallbackQuery.Event,
                       set_user_default: bool,
                       *_,
                       lang: Optional[str] = None,
                       **__):
    # callback data = set={sub_id}[,{action}[,{param}]][|{page_number}]
    # or set_default[={action}[,{param}]]
    """
    send_mode: -1=force link, 0=auto, 1=force Telegraph, 2=force message
    length_limit: Telegraph length limit, valid when send_mode==0. If exceeded, send via Telegraph; If is 0,
        send via Telegraph when a post cannot be sent in a single message
    link_preview: 0=auto, 1=force enable
    display_author: -1=disable, 0=auto, 1=force display
    display_via: -2=completely disable, -1=disable but display link, 0=auto, 1=force display
    display_title: -1=disable, 0=auto, 1=force display
    style: 0=RSStT, 1=flowerss
    """
    sub_id, action, param, page = parse_customization_callback_data(event.data)

    if sub_id is None and not set_user_default:
        await cmd_set_or_callback_get_set_page.__wrapped__(event, lang=lang, page=page)
        return

    sub_or_user: Union[db.Sub, db.User] = (
        await db.Sub.get_or_none(id=sub_id, user=event.chat_id).prefetch_related('feed', 'user')
        if not set_user_default
        else await db.User.get_or_none(id=event.chat_id)
    )
    if sub_or_user is None:
        await event.edit(i18n[lang]['subscription_not_exist'])
        return

    if (
            action is None
            or (action in {'interval', 'length_limit'} and (isinstance(param, int) or param == 'default'))
            or action == 'activate' and not set_user_default
            or action in inner.customization.SUB_OPTIONS_EXHAUSTIVE_VALUES
    ):
        if action == 'interval' and (isinstance(param, int) or param == 'default'):
            await inner.customization.set_interval(sub_or_user, param if param != 'default' else -100)
        elif action == 'length_limit' and (isinstance(param, int) or param == 'default'):
            await inner.customization.set_length_limit(sub_or_user, param if param != 'default' else -100)
        elif action == 'activate' and not set_user_default:
            await inner.customization.set_sub_activate(sub_or_user)
        elif action == 'display_media' and not set_user_default and \
                (sub_or_user.send_mode if sub_or_user.send_mode != -100 else sub_or_user.user.send_mode) in {1, -1}:
            await event.answer(i18n[lang]['display_media_only_effective_if_send_mode_auto_and_telegram'],
                               alert=True)
            return
        elif action is not None and action in inner.customization.SUB_OPTIONS_EXHAUSTIVE_VALUES:
            await inner.customization.set_exhaustive_option(sub_or_user, action)

        info = await inner.customization.get_sub_info(sub_or_user, lang, including_default_prompt=True) \
            if not set_user_default else \
            i18n[lang]['set_user_default_description']
        buttons = await inner.customization.get_customization_buttons(sub_or_user, lang=lang, page=page)
        await event.edit(info, buttons=buttons, parse_mode='html')
        return

    if action == 'interval':
        msg = i18n[lang]['set_interval_prompt']
        buttons = await inner.customization.get_set_interval_buttons(sub_or_user, lang=lang, page=page)
        await event.edit(msg, buttons=buttons)
        return
    if action == 'length_limit':
        if not set_user_default and \
                (sub_or_user.send_mode if sub_or_user.send_mode != -100 else sub_or_user.user.send_mode) != 0:
            await event.answer(i18n[lang]['length_limit_only_effective_if_send_mode_auto'], alert=True)
            return
        msg = i18n[lang]['set_length_limit_prompt']
        buttons = await inner.customization.get_set_length_limit_buttons(sub_or_user, lang=lang, page=page)
        await event.edit(msg, buttons=buttons)
        return

    await event.edit(i18n[lang]['action_invalid'])
    return


@command_gatekeeper(only_manager=False)
async def cmd_set_default(event: Union[events.NewMessage.Event, Message],
                          *_,
                          lang: Optional[str] = None,
                          **__):  # cmd: set_default
    user = await db.User.get_or_none(id=event.chat_id)
    msg = i18n[lang]['set_user_default_description']
    buttons = await inner.customization.get_customization_buttons(user, lang=lang)
    await event.respond(msg, buttons=buttons, parse_mode='html')
    return


@command_gatekeeper(only_manager=False)
async def callback_reset(event: events.CallbackQuery.Event,
                         *_,
                         lang: Optional[str] = None,
                         **__):  # callback data = reset={sub_id}
    sub_id, _, _, page = parse_customization_callback_data(event.data)
    sub = await db.Sub.get_or_none(id=sub_id, user=event.chat_id)
    if sub is None:
        await event.answer(i18n[lang]['subscription_not_exist'])
        return

    update_interval_flag = False
    if sub.interval is not None:
        sub.interval = None
        update_interval_flag = True
    sub.length_limit = sub.notify = sub.send_mode = sub.link_preview = sub.display_author = sub.display_media = \
        sub.display_title = sub.display_via = sub.style = -100
    await sub.save()
    if update_interval_flag:
        await inner.utils.update_interval(sub)
    info = await inner.customization.get_sub_info(sub, lang, including_default_prompt=True)
    buttons = await inner.customization.get_customization_buttons(sub, lang=lang, page=page)
    await event.edit(info, buttons=buttons, parse_mode='html')


@command_gatekeeper(only_manager=False)
async def callback_reset_all_confirm(event: events.CallbackQuery.Event,
                                     *_,
                                     lang: Optional[str] = None,
                                     **__):  # callback data = reset_all_confirm
    user_id = event.chat_id
    if await inner.utils.have_subs(user_id):
        await event.edit(
            i18n[lang]['reset_all_confirm_prompt'],
            buttons=[
                [Button.inline(i18n[lang]['reset_all_confirm'], data='reset_all')],
                [Button.inline(i18n[lang]['reset_all_cancel'], data='set_default')]
            ]
        )
        return
    await event.edit(i18n[lang]['no_subscription'])


@command_gatekeeper(only_manager=False)
async def callback_reset_all(event: events.CallbackQuery.Event,
                             *_,
                             lang: Optional[str] = None,
                             **__):  # callback data = reset_all
    user_id = event.chat_id
    subs = await db.Sub.filter(user=user_id)
    tasks = []
    for sub in subs:
        if sub.interval is not None:
            sub.interval = None
            tasks.append(inner.utils.update_interval(sub))
        sub.interval = None
        sub.length_limit = sub.notify = sub.send_mode = sub.link_preview = sub.display_author = sub.display_media = \
            sub.display_title = sub.display_via = sub.style = -100
    await db.Sub.bulk_update(subs, ('interval', 'length_limit', 'notify', 'send_mode', 'link_preview', 'display_author',
                                    'display_media', 'display_title', 'display_via', 'style'))
    for task in tasks:
        env.loop.create_task(task)
    await event.edit(i18n[lang]['reset_all_successful'])


@command_gatekeeper(only_manager=False)
async def cmd_activate_or_deactivate_subs(event: Union[events.NewMessage.Event, Message],
                                          activate: bool,
                                          *_,
                                          lang: Optional[str] = None,
                                          **__):  # cmd: activate_subs | deactivate_subs
    await callback_get_activate_or_deactivate_page.__wrapped__(event, activate, lang=lang, page=1)


@command_gatekeeper(only_manager=False)
async def callback_get_activate_or_deactivate_page(event: Union[events.CallbackQuery.Event,
                                                                events.NewMessage.Event,
                                                                Message],
                                                   activate: bool,
                                                   *_,
                                                   lang: Optional[str] = None,
                                                   page: Optional[int] = None,
                                                   **__):  # callback data: get_(activate|deactivate)_page|{page}
    event_is_msg = not isinstance(event, events.CallbackQuery.Event)
    if page is None:
        page = int(parse_callback_data_with_page(event.data)[1]) if not event_is_msg else 1
    have_subs = await inner.utils.have_subs(event.chat_id)
    if not have_subs:
        no_subscription_msg = i18n[lang]['no_subscription']
        await (event.respond(no_subscription_msg) if event_is_msg
               else event.edit(no_subscription_msg))
        return
    sub_buttons = await inner.utils.get_sub_choosing_buttons(
        event.chat_id,
        page_number=page,
        callback='activate_sub' if activate else 'deactivate_sub',
        get_page_callback='get_activate_page' if activate else 'get_deactivate_page',
        lang=lang,
        rows=11,
        state=0 if activate else 1
    )
    msg = i18n[lang]['choose_sub_to_be_activated' if sub_buttons else 'all_subs_are_activated'] if activate \
        else i18n[lang]['choose_sub_to_be_deactivated' if sub_buttons else 'all_subs_are_deactivated']
    activate_or_deactivate_all_subs_str = 'activate_all_subs' if activate else 'deactivate_all_subs'
    buttons = (
            (
                (Button.inline(i18n[lang][activate_or_deactivate_all_subs_str],
                               data=activate_or_deactivate_all_subs_str),),
            )
            + sub_buttons
    ) if sub_buttons else None
    await (event.respond(msg, buttons=buttons) if event_is_msg
           else event.edit(msg, buttons=buttons))


@command_gatekeeper(only_manager=False)
async def callback_activate_or_deactivate_all_subs(event: events.CallbackQuery.Event,
                                                   activate: bool,
                                                   *_,
                                                   lang: Optional[str] = None,
                                                   **__):  # callback data: (activate|deactivate)_all_subs
    await inner.utils.activate_or_deactivate_all_subs(event.chat_id, activate=activate)
    await callback_get_activate_or_deactivate_page.__wrapped__(event, activate, lang=lang, page=1)


@command_gatekeeper(only_manager=False)
async def callback_activate_or_deactivate_sub(event: events.CallbackQuery.Event,
                                              activate: bool,
                                              *_,
                                              lang: Optional[str] = None,
                                              **__):  # callback data: (activate|deactivate)_sub={id}|{page}
    sub_id, page = parse_callback_data_with_page(event.data)
    sub_id = int(sub_id)
    unsub_res = await inner.utils.activate_or_deactivate_sub(event.chat_id, sub_id, activate=activate)
    if unsub_res is None:
        await event.answer('ERROR: ' + i18n[lang]['subscription_not_exist'], alert=True)
        return
    await callback_get_activate_or_deactivate_page.__wrapped__(event, activate, lang=lang, page=page)


@command_gatekeeper(only_manager=False)
async def callback_del_subs_title(event: events.CallbackQuery.Event,
                                  *_,
                                  **__):  # callback data: del_subs_title={id_start}-{id_end}|{id_start}-{id_end}|...
    user_id = event.chat_id
    id_ranges_str = event.data.decode().strip().split('=')[-1].split('|')
    subs = []
    for id_range_str in id_ranges_str:
        id_range = id_range_str.split('-')
        id_start = int(id_range[0])
        id_end = int(id_range[1])
        subs.extend(await db.Sub.filter(user_id=user_id, id__range=(id_start, id_end)).all())
    await inner.customization.del_subs_title(subs)
    await misc.callback_del_buttons.__wrapped__(event)


@command_gatekeeper(only_manager=False)
async def cmd_set_title(event: Union[events.NewMessage.Event, Message],
                        *_,
                        lang: Optional[str] = None,
                        **__):
    sub, title = await parse_command_get_sub_or_user_and_param(event.raw_text, event.chat_id, max_split=2)
    title = title.strip() if title else None
    if not sub:
        await event.respond(i18n[lang]['permission_denied_no_direct_use'])
        return
    if not title and not sub.title:
        await event.respond(i18n[lang]['cmd_set_title_usage_prompt_html'], parse_mode='html')
        return
    await inner.customization.set_sub_title(sub, title)
    await event.respond(
        (
                ((i18n[lang]['set_title_success'] + '\n' + f'<code>{escape_html(title)}</code>')
                 if title
                 else i18n[lang]['set_title_success_cleared'])
                + '\n\n' +
                await inner.customization.get_sub_info(sub, lang=lang)
        ),
        buttons=(Button.inline(i18n[lang]['other_settings_button'], data=f'set={sub.id}'),),
        parse_mode='html')


@command_gatekeeper(only_manager=False)
async def cmd_set_interval(event: Union[events.NewMessage.Event, Message],
                           *_,
                           lang: Optional[str] = None,
                           **__):
    sub_or_user, interval = await parse_command_get_sub_or_user_and_param(event.raw_text, event.chat_id,
                                                                          allow_setting_user_default=True)
    interval = int(interval) if interval and interval.isdigit() and int(interval) >= 1 else None
    minimal_interval = db.EffectiveOptions.minimal_interval
    if not sub_or_user:
        await event.respond(i18n[lang]['permission_denied_no_direct_use'])
        return
    if not interval:
        await event.respond(i18n[lang]['cmd_set_interval_usage_prompt_html'], parse_mode='html')
        return
    if interval < minimal_interval and event.chat_id != env.MANAGER:
        await event.respond(i18n[lang]['set_interval_failure_too_small_html'] % minimal_interval,
                            parse_mode='html')
        return
    await inner.customization.set_interval(sub_or_user, interval)
    await event.respond(
        (
            ((i18n[lang]['set_interval_success_html'] % (interval,))
             + '\n\n' +
             await inner.customization.get_sub_info(sub_or_user, lang=lang))
            if isinstance(sub_or_user, db.Sub)
            else i18n[lang]['set_default_interval_success_html'] % (interval,)
        ),
        buttons=(Button.inline(i18n[lang]['other_settings_button'],
                               data=f'set={sub_or_user.id}' if isinstance(sub_or_user, db.Sub) else f'set_default'),),
        parse_mode='html')


@command_gatekeeper(only_manager=False)
async def cmd_set_hashtags(event: Union[events.NewMessage.Event, Message],
                           *_,
                           lang: Optional[str] = None,
                           **__):
    sub, hashtags = await parse_command_get_sub_or_user_and_param(event.raw_text, event.chat_id, max_split=2)
    hashtags = inner.utils.parse_hashtags(hashtags) if hashtags else None
    if not sub:
        await event.respond(i18n[lang]['permission_denied_no_direct_use'])
        return
    if not hashtags and not sub.tags:
        await event.respond(i18n[lang]['cmd_set_hashtags_usage_prompt_html'], parse_mode='html')
        return
    hashtags_str = ' '.join(hashtags) if hashtags else None
    if hashtags_str and len(hashtags_str) > 255:
        await event.respond(i18n[lang]['set_hashtags_failure_too_many'])
        return
    await inner.customization.set_sub_hashtags(sub, hashtags_str)
    await event.respond(
        (
                ((i18n[lang]['set_hashtags_success_html'] + '\n'
                  + f'<b>{inner.utils.construct_hashtags(hashtags)}</b>')
                 if hashtags
                 else i18n[lang]['set_hashtags_success_cleared'])
                + '\n\n' +
                await inner.customization.get_sub_info(sub, lang=lang)
        ),
        buttons=(Button.inline(i18n[lang]['other_settings_button'], data=f'set={sub.id}'),),
        parse_mode='html')
