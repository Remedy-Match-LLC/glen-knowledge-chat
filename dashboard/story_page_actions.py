"""Console actions for story pages: create, edit, the three gate steps, withdraw.

Mirrors dashboard/mentor_page_actions.py and runs through the same /api/action
dispatch (permission check + audit event).

Who may do what:
  create, edit, withdraw          owner or ops
  step 1 compliance (checked)     owner or ops
  step 2 giver approved           owner or ops
  step 3 publish                  Glen only: the owner role AND the master console key.
                                  Rae's per-user token also carries the owner role, so
                                  the role alone would let her publish; the spec says
                                  step 3 is Glen's.
"""
from dashboard.actions import register_action, Action, LOW_WRITE, get_action
from dashboard.rbac import OWNER, OPS
from dashboard import story_pages as _sp

# The master console key is Glen's. Stamps record his name, not the key's label.
MASTER_PERSON = "glen"


def _is_glen(actor):
    return (actor is not None and getattr(actor, "role", "") == OWNER
            and bool(getattr(actor, "master", False)))


def _actor_name(actor):
    """A person's name for the stamps: "glen" for the master key, else the workspace
    user name _bos_actor resolved. Never a token or part of one."""
    if _is_glen(actor):
        return MASTER_PERSON
    return (getattr(actor, "name", "") or "unknown-user")


def _slug(params):
    slug = (params.get("slug") or "").strip().lower()
    if not slug:
        raise ValueError("slug required")
    return slug


def _result(page):
    return {"ok": True, "slug": page["story_slug"], "state": page["state"],
            "content_hash": page["current_hash"]}


def _refused(slug, e):
    return {"ok": False, "slug": slug, "error": e.code, "detail": e.detail}


def _exec_create(params, ctx):
    slug = _slug(params)
    try:
        page = _sp.create(ctx["cx"], slug,
                          testimonial_id=params.get("testimonial_id"),
                          content=params.get("content"),
                          name_line=params.get("name_line") or "",
                          ref_slug=params.get("ref_slug") or "",
                          by=_actor_name(ctx.get("actor")))
    except _sp.StoryError as e:
        return _refused(slug, e)
    return _result(page)


def _exec_edit(params, ctx):
    slug = _slug(params)
    kw = {k: params[k] for k in ("content", "name_line", "ref_slug", "testimonial_id")
          if k in params}
    try:
        page = _sp.update(ctx["cx"], slug, expected_hash=params.get("content_hash") or "",
                          by=_actor_name(ctx.get("actor")), **kw)
    except _sp.StoryError as e:
        return _refused(slug, e)
    return _result(page)


def _exec_check(params, ctx):
    slug = _slug(params)
    try:
        page = _sp.mark_checked(ctx["cx"], slug, by=_actor_name(ctx.get("actor")),
                                note=params.get("note") or "",
                                content_hash=params.get("content_hash") or "")
    except _sp.StoryError as e:
        return _refused(slug, e)
    return _result(page)


def _exec_giver_approve(params, ctx):
    slug = _slug(params)
    try:
        page = _sp.mark_giver_approved(ctx["cx"], slug, by=_actor_name(ctx.get("actor")),
                                       consent_ref=params.get("consent_ref") or "",
                                       content_hash=params.get("content_hash") or "")
    except _sp.StoryError as e:
        return _refused(slug, e)
    return _result(page)


def _exec_publish(params, ctx):
    slug = _slug(params)
    actor = ctx.get("actor")
    if not _is_glen(actor):
        return {"ok": False, "slug": slug, "error": "owner_only",
                "detail": "step 3 is Glen's, with the master console key"}
    try:
        page = _sp.mark_published(ctx["cx"], slug, by=MASTER_PERSON,
                                  content_hash=params.get("content_hash") or "")
    except _sp.StoryError as e:
        return _refused(slug, e)
    return _result(page)


def _exec_withdraw(params, ctx):
    slug = _slug(params)
    try:
        page = _sp.withdraw(ctx["cx"], slug, by=_actor_name(ctx.get("actor")))
    except _sp.StoryError as e:
        return _refused(slug, e)
    return _result(page)


def register():
    if get_action("story_page.publish"):
        return
    staff = (OWNER, OPS)
    for key, title, desc, perm, fn in (
        ("story_page.create", "Create story page",
         "Create a draft story page from a consented testimonial row.", staff, _exec_create),
        ("story_page.edit", "Edit story page",
         "Edit the story, links, name line or ref slug (resets to draft).", staff, _exec_edit),
        ("story_page.check", "Story page step 1: compliance",
         "Record the compliance check (draft to checked).", staff, _exec_check),
        ("story_page.giver_approve", "Story page step 2: giver approved",
         "Record the giver's written approval of the page (checked to giver_approved).",
         staff, _exec_giver_approve),
        ("story_page.publish", "Story page step 3: publish",
         "Glen publishes the page (giver_approved to published).", (OWNER,), _exec_publish),
        ("story_page.withdraw", "Withdraw story page",
         "Take a story page down at once; the record is kept.", staff, _exec_withdraw),
    ):
        register_action(Action(key=key, module="story_pages", title=title, description=desc,
                               risk_tier=LOW_WRITE, permission=perm, executor=fn))
