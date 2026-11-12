"""E4a: the payment-link actions on the fees and dues screens, as tags so ``fees.html`` and ``dues.html`` change by a line each."""

from django import template

from .. import paylinks

register = template.Library()


def _lang(context):
    return context.get("lang", "ar")


def _board(context):
    return paylinks.board_for(context["request"], _lang(context))


def _cell(context, state, board):
    return {"s": state, "pl": board.words, "lang": board.lang, "csrf_token": context.get("csrf_token"),
            "next": f"{context['request'].get_full_path()}#pay-{state.invoice.pk}"}


@register.inclusion_tag("education/_paylink_head.html", takes_context=True)
def fee_pay_head(context):
    board = _board(context)
    return {"shown": board.shown, "pl": board.words}


@register.inclusion_tag("education/_paylink_cell.html", takes_context=True)
def fee_pay_cell(context, invoice):
    """The «لينك الدفع» cell of one invoice row on the fees screen (nothing when the actions are not offered)."""

    board = _board(context)
    return {"shown": board.shown, **(_cell(context, board.state(invoice), board) if board.shown else {})}


@register.inclusion_tag("education/_paylink_payer.html", takes_context=True)
def payer_pay_actions(context, payer):
    """The dues screen: a small list of the payer's open posted fee invoices, each with the same action."""

    board = _board(context)
    states = board.payer_states(payer.pk) if board.shown else []
    return {"cells": [_cell(context, state, board) for state in states], "pl": board.words, "lang": board.lang}


@register.inclusion_tag("education/_paylink_note.html", takes_context=True)
def fee_pay_note(context):
    """One line, once per screen, for whoever can set online payment up: why no action is offered and where to turn it on."""

    from permissions.services import user_has_permission

    board = _board(context)
    note = ""
    if board.problem_code:
        if user_has_permission(context["request"].user, "settings.manage_settings") and _has_open_posted():
            note = board.message()
    elif board.can_make and board.load().problems:
        note = board.problems[0]
    return {"note": note, "off": board.problem_code == "off", "lang": board.lang, "pl": board.words}


def _has_open_posted():
    """When the actions are off nothing is loaded, so ask the one question the note needs: is there a posted fee invoice with money due."""

    return paylinks.fee_invoices().filter(status="posted", remaining_due__gt=0).exists()


@register.inclusion_tag("education/_paylink_bulk.html", takes_context=True)
def fee_pay_bulk(context, rows, first):
    """«اعمل لينكات دفع للشهر» under the month's billed invoices: how many are ready, and the cap."""

    board = _board(context)
    ready = sum(1 for row in rows if board.shown and board.state(row["invoice"]).kind == "ready") if board.shown else 0
    return {"shown": board.shown and ready > 0, "ready": ready, "hint": board.words["bulk_hint"].format(cap=paylinks.BULK_CAP), "ready_text": board.words["ready_count"].format(n=ready), "pl": board.words, "lang": board.lang,
            "month": first, "csrf_token": context.get("csrf_token")}
