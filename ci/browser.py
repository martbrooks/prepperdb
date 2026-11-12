"""Real-browser acceptance with synthetic data and WAN access denied by the browser."""

import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time

from playwright.sync_api import sync_playwright, expect
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "test-results"
ARTIFACTS.mkdir(exist_ok=True)


def assert_labels(page):
    missing = page.locator("input:not([type=hidden]),select,textarea").evaluate_all("""elements => elements.filter(e => e.getClientRects().length).flatMap(e => {
        const issues = [];
        if (!(e.labels?.length || e.getAttribute('aria-label'))) issues.push(e.id + ': missing label');
        for (const id of (e.getAttribute('aria-describedby') || '').split(/\\s+/).filter(Boolean)) {
            if (!document.getElementById(id)) issues.push(e.id + ': missing description ' + id);
        }
        return issues;
    })""")
    assert not missing, missing


def check_tree_fallbacks(page, base, root):
    browser = page.context.browser
    state = page.context.storage_state()
    for javascript in (False, True):
        context = browser.new_context(
            storage_state=state, java_script_enabled=javascript, viewport=page.viewport_size
        )
        context.route(
            "**/*",
            lambda route: (
                route.continue_() if route.request.url.startswith(base + "/") else route.abort()
            ),
        )
        if javascript:
            context.add_init_script(
                "Object.defineProperty(window, 'localStorage', {get() {throw new DOMException('Unavailable', 'SecurityError');}})"
            )
        fallback = context.new_page()
        errors = []
        fallback.on("pageerror", lambda error: errors.append(str(error)))
        fallback.goto(base + "/categories/")
        edit = fallback.get_by_role("link", name="Edit " + root + " / Alpha", exact=True)
        expect(edit).to_be_visible()
        if javascript:
            toggle = fallback.get_by_role("button", name=root, exact=True)
            toggle.click()
            expect(edit).to_be_hidden()
            toggle.click()
            expect(edit).to_be_visible()
        else:
            expect(fallback.get_by_role("button", name="Expand all", exact=True)).to_have_count(0)
            edit.click()
            expect(
                fallback.get_by_role("heading", name="Edit category", exact=True)
            ).to_be_visible()
            expect(fallback.get_by_label("Name:", exact=True)).to_have_value("Alpha")
        assert not errors, errors
        context.close()


def check_stock_layouts(page, base, name, root, item):
    page.goto(base + "/?view=tiles")
    folder = page.get_by_role("button", name=root, exact=True)
    link = page.get_by_role("link", name=item, exact=True)
    folder.press("ArrowLeft")
    expect(link).to_be_hidden()
    page.reload()
    expect(folder).to_have_attribute("aria-expanded", "false")
    expect(page.locator(".stock-browser")).to_have_class(re.compile(r"stock-list"))
    expect(page.get_by_role("link", name="Tiles", exact=True)).to_have_count(0)
    expect(link).to_be_hidden()
    # Search reveals matches even when their normal category branch is folded.
    page.get_by_label("Find in stock", exact=True).fill(item)
    page.get_by_role("button", name="Search", exact=True).click()
    expect(link).to_be_visible()
    expect(page.get_by_label("Find in stock", exact=True)).to_have_value(item)
    expect(link).to_be_visible()
    page.goto(base)
    expect(link).to_be_hidden()
    folder.press("ArrowRight")
    expect(link).to_be_visible()
    row = page.locator(".stock-item").filter(has=page.get_by_role("link", name=item, exact=True))
    expect(row.locator(".stock-date")).to_contain_text("Best before unknown")
    increase = page.get_by_role("button", name="Increase stock of " + item + " by one", exact=True)
    increase.scroll_into_view_if_needed()
    before_scroll = page.evaluate("scrollY")
    increase.click()
    expect(row.locator(".stock-quantity")).to_have_text("2")
    assert abs(page.evaluate("scrollY") - before_scroll) <= 2
    expect(page.locator(".notice")).to_have_count(0)
    expect(increase).to_be_focused()
    page.get_by_role("link", name="History", exact=True).click()
    latest = page.locator(".history-entry").first
    expect(latest).to_contain_text(item)
    expect(latest).to_contain_text("Added: 1 → 2")
    latest.get_by_role("button", name="Undo this change").click()
    expect(page.locator(".history-entry").first).to_contain_text("Undone: 2 → 1")
    page.goto(base)
    expect(row.locator(".stock-quantity")).to_have_text("1")
    dialogs = []

    def cancel_last(dialog):
        dialogs.append(dialog.message)
        dialog.dismiss()

    page.once("dialog", cancel_last)
    page.get_by_role("button", name="Use one " + item, exact=True).click()
    expect(row.locator(".stock-quantity")).to_have_text("1")
    assert len(dialogs) == 1 and item in dialogs[0] and "none in stock" in dialogs[0]
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Use one " + item, exact=True).click()
    expect(row).to_have_count(0)
    page.goto(base + "/out-of-stock/")
    expect(row.locator(".stock-quantity")).to_have_text("0")
    expect(row.get_by_role("link", name="Add stock for " + item, exact=True)).to_be_visible()
    expect(row.get_by_role("button")).to_have_count(0)
    expect(page.locator(".cards, .card")).to_have_count(0)
    saved_viewport = page.viewport_size
    for width in (320, 390, 768, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
            "out-of-stock",
            width,
        )
    page.set_viewport_size(saved_viewport)
    page.screenshot(path=str(ARTIFACTS / (name + "-out-of-stock-list.png")), full_page=True)
    page.goto(base + "/attention/")
    expect(row.locator(".stock-quantity")).to_have_text("0")
    page.get_by_role("link", name="History", exact=True).click()
    page.locator(".history-entry").first.get_by_role("button", name="Undo this change").click()
    expect(page.locator(".history-entry").first).to_contain_text("Undone: 0 → 1")
    page.goto(base)
    expect(row.locator(".stock-quantity")).to_have_text("1")
    original = page.viewport_size
    for width in (320, 390, 768, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
    page.set_viewport_size(original)
    page.screenshot(path=str(ARTIFACTS / f"{name}-stock-list.png"), full_page=True)
    # Stock disclosure uses the management fallback contract with independent storage.
    state = page.context.storage_state()
    for javascript in (False, True):
        context = page.context.browser.new_context(
            storage_state=state, java_script_enabled=javascript, viewport=page.viewport_size
        )
        context.route(
            "**/*",
            lambda route: (
                route.continue_() if route.request.url.startswith(base + "/") else route.abort()
            ),
        )
        if javascript:
            context.add_init_script(
                "Object.defineProperty(window, 'localStorage', {get() {throw new DOMException('Unavailable', 'SecurityError');}})"
            )
        fallback = context.new_page()
        fallback.goto(base + "/?view=list")
        expect(fallback.get_by_role("link", name=item, exact=True)).to_be_visible()
        if javascript:
            fallback.get_by_role("button", name=root, exact=True).click()
            expect(fallback.get_by_role("link", name=item, exact=True)).to_be_hidden()
        else:
            expect(fallback.get_by_role("button", name="Collapse all", exact=True)).to_have_count(0)
            expect(fallback.locator(".stock-browser")).to_have_class(re.compile(r"stock-list"))
        if not javascript:
            fallback.get_by_role(
                "button", name="Increase stock of " + item + " by one", exact=True
            ).click()
            expect(
                fallback.locator(".stock-item")
                .filter(has=fallback.get_by_role("link", name=item, exact=True))
                .locator(".stock-quantity")
            ).to_have_text("2")
            fallback.get_by_role("link", name="History", exact=True).click()
            fallback.locator(".history-entry").first.get_by_role(
                "button", name="Undo this change"
            ).click()
            expect(fallback.locator(".history-entry").first).to_contain_text("Undone: 2 → 1")
            fallback.goto(base + "/?q=" + item)
            use = fallback.get_by_role("button", name="Use one " + item, exact=True)
            use.click()
            expect(
                fallback.get_by_role("heading", name="Use last item?", exact=True)
            ).to_be_visible()
            fallback.get_by_role("link", name="Cancel", exact=True).click()
            expect(fallback.locator(".stock-quantity")).to_have_text("1")
            use.click()
            fallback.get_by_role("button", name="Use last item", exact=True).click()
            expect(
                fallback.get_by_role("heading", name="No matching stock", exact=True)
            ).to_be_visible()
            fallback.goto(base + "/history/")
            fallback.locator(".history-entry").first.get_by_role(
                "button", name="Undo this change"
            ).click()
            expect(fallback.locator(".history-entry").first).to_contain_text("Undone: 0 → 1")
        context.close()
    page.goto(base + "/?view=tiles&q=" + item)


def check_adjustment_bounds(page, base, name):
    item = "Discard bounds " + name
    page.goto(base + "/add/")
    page.get_by_label("Name:", exact=True).fill(item)
    page.get_by_label("Quantity (packs or individual items):", exact=True).fill("2")
    page.get_by_role("button", name="Save stock", exact=True).click()
    expect(page).to_have_url(re.compile(r"/products/\d+/$"))
    product_url = page.url
    page.get_by_role("link", name="Use / set / discard", exact=True).click()
    adjust_url = page.url
    action = page.get_by_label("Action:", exact=True)
    amount = page.get_by_label("Quantity:", exact=True)
    increase = page.get_by_role("button", name="Increase Quantity", exact=True)
    for choice in ("consume", "discard"):
        action.select_option(choice)
        expect(amount).to_have_attribute("min", "1")
        expect(amount).to_have_attribute("max", "2")
        amount.fill("1")
        increase.click()
        expect(amount).to_have_value("2")
        expect(increase).to_be_disabled()
    amount.fill("14")
    assert amount.evaluate("e => e.validity.rangeOverflow")
    posts = []
    page.on(
        "request",
        lambda request: (
            posts.append(request.url)
            if request.method == "POST" and request.url == adjust_url
            else None
        ),
    )
    page.get_by_role("button", name="Update quantity", exact=True).click()
    assert not posts, posts
    action.select_option("set")
    expect(amount).to_have_attribute("min", "0")
    expect(amount).to_have_attribute("max", "1000000")
    expect(amount).to_have_value("14")
    assert amount.evaluate("e => e.validity.valid")
    action.select_option("discard")
    expect(amount).to_have_value("14")
    assert amount.evaluate("e => e.validity.rangeOverflow")
    amount.fill("2")
    page.screenshot(path=str(ARTIFACTS / (name + "-discard-limit.png")), full_page=True)
    # Without client scripting, server validation still rejects over-discard;
    # Set quantity must remain able to raise the count above available stock.
    context = page.context.browser.new_context(
        storage_state=page.context.storage_state(),
        java_script_enabled=False,
        viewport=page.viewport_size,
    )
    context.route(
        "**/*",
        lambda route: (
            route.continue_() if route.request.url.startswith(base + "/") else route.abort()
        ),
    )
    fallback = context.new_page()
    fallback.goto(adjust_url)
    fallback.get_by_label("Action:", exact=True).select_option("discard")
    fallback.get_by_label("Quantity:", exact=True).fill("14")
    fallback.get_by_role("button", name="Update quantity", exact=True).click()
    expect(
        fallback.get_by_text("This batch has only 2 items. Enter 2 or fewer.", exact=True)
    ).to_be_visible()
    fallback.get_by_label("Action:", exact=True).select_option("set")
    fallback.get_by_role("button", name="Update quantity", exact=True).click()
    expect(fallback).to_have_url(product_url)
    expect(fallback.locator(".big-quantity")).to_contain_text("14")
    context.close()
    page.goto(adjust_url)
    action.select_option("discard")
    expect(amount).to_have_attribute("max", "14")
    amount.fill("14")
    page.get_by_role("button", name="Update quantity", exact=True).click()
    expect(page).to_have_url(product_url)
    expect(page.locator(".big-quantity")).to_contain_text("0")
    page.goto(adjust_url)
    expect(action).to_have_value("set")
    expect(amount).to_have_value("0")
    action.select_option("discard")
    expect(page.get_by_role("button", name="Update quantity", exact=True)).to_be_disabled()
    expect(
        page.get_by_text(
            "This batch is empty. Choose Set quantity to record a correction.", exact=True
        )
    ).to_be_visible()
    page.get_by_role("link", name="History", exact=True).click()
    entry = (
        page.locator(".history-entry")
        .filter(has=page.get_by_role("link", name=item, exact=True))
        .first
    )
    expect(entry).to_contain_text("Discarded: 14 → 0")
    entry.get_by_role("button", name="Undo this change", exact=True).click()
    expect(entry).to_contain_text("Undone: 0 → 14")
    page.goto(product_url)
    page.get_by_role("link", name="Delete item permanently", exact=True).click()
    page.get_by_role("button", name="Delete item", exact=True).click()
    expect(page).to_have_url(base + "/")


def check_attention_delete(page, base, name):
    item = "Delete shortcut " + name
    page.goto(base + "/add/")
    page.get_by_label("Name:", exact=True).fill(item)
    page.get_by_role("button", name="Save stock", exact=True).click()
    expect(page).to_have_url(re.compile(r"/products/\d+/$"))
    product_url = page.url
    page.goto(base + "/attention/")
    row = page.locator(".stock-item").filter(has=page.get_by_role("link", name=item, exact=True))
    delete = row.get_by_role("link", name="Delete " + item + " permanently", exact=True)
    expect(delete).to_have_count(0)
    page.goto(product_url)
    page.get_by_role("link", name="Use / set / discard", exact=True).click()
    page.get_by_label("Action:", exact=True).select_option("set")
    page.get_by_label("Quantity:", exact=True).fill("0")
    page.get_by_role("button", name="Update quantity", exact=True).click()
    expect(page).to_have_url(product_url)
    page.goto(base + "/attention/")
    expect(delete).to_be_visible()
    expect(delete.locator("img")).to_have_js_property("naturalWidth", 24)
    assert delete.evaluate("e => e.offsetWidth >= 44 && e.offsetHeight >= 44")
    expect(row.locator(".stock-actions a").first).to_have_attribute(
        "aria-label", "Add stock for " + item
    )
    original = page.viewport_size
    for width in (320, 390, 768, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
    page.set_viewport_size(original)
    page.screenshot(path=str(ARTIFACTS / (name + "-attention-delete.png")), full_page=True)
    # Both kinds of browser can cancel the ordinary confirmation page.
    context = page.context.browser.new_context(
        storage_state=page.context.storage_state(), java_script_enabled=False, viewport=original
    )
    context.route(
        "**/*",
        lambda route: (
            route.continue_() if route.request.url.startswith(base + "/") else route.abort()
        ),
    )
    fallback = context.new_page()
    fallback.goto(base + "/attention/")
    fallback.get_by_role("link", name="Delete " + item + " permanently", exact=True).click()
    expect(
        fallback.get_by_text(
            "You have 0 items of this in stock. Are you sure you want to remove this record?",
            exact=True,
        )
    ).to_be_visible()
    fallback.get_by_role("link", name="Cancel", exact=True).click()
    expect(fallback).to_have_url(base + "/attention/")
    context.close()
    delete.click()
    page.get_by_role("link", name="Cancel", exact=True).click()
    expect(page).to_have_url(base + "/attention/")
    expect(row).to_be_visible()
    delete.click()
    page.get_by_role("button", name="Delete item", exact=True).click()
    expect(page).to_have_url(base + "/attention/")
    expect(row).to_have_count(0)
    page.goto(base + "/out-of-stock/")
    expect(row).to_have_count(0)
    assert page.request.get(product_url).status == 404
    page.goto(base)


def check_item_views(page, base, name, item, has_photo=False):
    product_url = page.url
    original = page.viewport_size
    for path, view in (("/", "stock"), ("/attention/", "attention"), ("/rotation/", "rotation")):
        page.goto(base + path)
        row = page.locator(".stock-item").filter(
            has=page.get_by_role("link", name=item, exact=True)
        )
        expect(row).to_be_visible()
        expect(page.locator(".cards, .card")).to_have_count(0)
        expect(row.locator(".stock-quantity")).to_have_text("6")
        picture = row.locator(".product-image")
        if has_photo:
            expect(picture).to_have_js_property("naturalWidth", 800)
        else:
            expect(picture).to_have_attribute("alt", "No product image")
        if path == "/attention/":
            plus = row.get_by_role(
                "button", name="Increase stock of " + item + " by one", exact=True
            )
            plus.scroll_into_view_if_needed()
            scroll = page.evaluate("scrollY")
            plus.click()
            expect(row.locator(".stock-quantity")).to_have_text("7")
            assert page.url == base + path
            assert abs(page.evaluate("scrollY") - scroll) <= 2
            row.get_by_role("button", name="Use one " + item, exact=True).click()
            expect(row.locator(".stock-quantity")).to_have_text("6")
            expect(page.locator(".notice")).to_have_count(0)
        for width in (320, 390, 768, 1280):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                view,
                width,
            )
        page.set_viewport_size(original)
        page.screenshot(
            path=str(ARTIFACTS / f"{name}-{view}-{'photo' if has_photo else 'placeholder'}.png"),
            full_page=True,
        )
    page.goto(product_url)


def check_photo_editor(page, base, name):
    product_url = page.url
    before = page.locator(".heading img").get_attribute("src")
    page.get_by_role("link", name="Manage photo", exact=True).click()
    expect(page.locator("canvas")).to_be_visible()
    assert_labels(page)
    page.get_by_label("Zoom:", exact=True).press("ArrowRight")
    page.get_by_label("Horizontal crop position:", exact=True).press("End")
    page.get_by_label("Rotate:", exact=True).select_option("90")
    expect(page.locator("[data-photo-status]")).to_contain_text("110%")
    page.get_by_role("button", name="Reset adjustments", exact=True).click()
    expect(page.get_by_label("Zoom:", exact=True)).to_have_value("1")
    expect(page.get_by_label("Rotate:", exact=True)).to_have_value("0")
    page.get_by_label("Zoom:", exact=True).press("ArrowRight")
    page.get_by_label("Vertical crop position:", exact=True).press("Home")
    page.get_by_label("Rotate:", exact=True).select_option("90")
    page.screenshot(path=str(ARTIFACTS / (name + "-photo-editor.png")), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("link", name="Cancel", exact=True).click()
    expect(page.locator(".heading img")).to_have_attribute("src", before)
    page.get_by_role("link", name="Manage photo", exact=True).click()
    expect(page.locator("canvas")).to_be_visible()
    page.get_by_label("Zoom:", exact=True).press("ArrowRight")
    page.get_by_label("Rotate:", exact=True).select_option("90")
    page.get_by_role("button", name="Save photo", exact=True).click()
    expect(page).to_have_url(product_url)
    expect(page.locator(".heading img")).not_to_have_attribute("src", before)
    expect(page.locator(".heading img")).to_have_js_property("naturalWidth", 800)
    expect(page.locator(".heading img")).to_have_js_property("naturalHeight", 800)
    # Existing-photo editing still submits ordinary forms without JavaScript.
    fallback_context = page.context.browser.new_context(
        storage_state=page.context.storage_state(),
        java_script_enabled=False,
        viewport=page.viewport_size,
    )
    fallback_context.route(
        "**/*",
        lambda route: (
            route.continue_() if route.request.url.startswith(base + "/") else route.abort()
        ),
    )
    fallback = fallback_context.new_page()
    fallback.goto(product_url + "photo/")
    expect(fallback.locator("[data-photo-fallback]")).to_be_visible()
    fallback.get_by_label("Rotate:", exact=True).select_option("180")
    fallback.get_by_role("button", name="Save photo", exact=True).click()
    expect(fallback).to_have_url(product_url)
    expect(fallback.locator(".heading img")).to_have_js_property("naturalWidth", 800)
    fallback_context.close()
    page.goto(product_url + "photo/")
    page.get_by_role("link", name="Remove photo", exact=True).click()
    page.get_by_role("link", name="Cancel", exact=True).click()
    expect(page.locator("canvas")).to_be_visible()
    page.get_by_role("link", name="Remove photo", exact=True).click()
    page.get_by_role("button", name="Remove photo", exact=True).click()
    expect(page).to_have_url(product_url)
    expect(page.locator(".heading img")).to_have_attribute("alt", "No product image")
    page.get_by_role("link", name="Add photo", exact=True).click()
    page.get_by_label("Product photo:", exact=True).set_input_files(
        str(ROOT / "stock/tests/fixtures/ean13.png")
    )
    expect(page.locator("canvas")).to_be_visible()
    page.get_by_role("button", name="Save photo", exact=True).click()
    expect(page).to_have_url(product_url)


def check_categories(page, base, name):
    root = "Browser root " + name
    item = "Categorised " + name
    page.get_by_role("link", name="Categories", exact=True).click()
    page.get_by_role("link", name="Add category", exact=True).click()
    page.get_by_label("Name:", exact=True).fill(root)
    page.get_by_role("button", name="Save category", exact=True).click()
    for child in ("Zebra", "Alpha"):
        page.get_by_role("link", name="Add child to " + root, exact=True).click()
        page.get_by_label("Name:", exact=True).fill(child)
        page.get_by_role("button", name="Save category", exact=True).click()
    folder = page.get_by_role("button", name=root, exact=True)
    expect(folder).to_have_attribute("aria-expanded", "true")
    children = page.locator("#" + folder.get_attribute("aria-controls"))
    expect(children.locator(":scope > li > .category-node-row > .category-label")).to_have_text(
        ["Alpha", "Zebra"]
    )
    folder.focus()
    page.keyboard.press("Space")
    expect(children).to_be_hidden()
    expect(page.get_by_role("link", name="Edit " + root, exact=True)).to_be_visible()
    page.reload()
    expect(folder).to_have_attribute("aria-expanded", "false")
    folder.press("Enter")
    expect(children).to_be_visible()
    folder.press("ArrowLeft")
    expect(children).to_be_hidden()
    folder.press("ArrowRight")
    expect(children).to_be_visible()
    page.get_by_role("button", name="Collapse all", exact=True).click()
    expect(children).to_be_hidden()
    page.get_by_role("button", name="Expand all", exact=True).click()
    expect(children).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(ARTIFACTS / (name + "-category-explorer.png")), full_page=True)
    check_tree_fallbacks(page, base, root)
    page.get_by_role("link", name="Edit " + root + " / Zebra", exact=True).click()
    page.get_by_label("Name:", exact=True).fill("Beta")
    page.get_by_label("Parent category:", exact=True).select_option(label=root + " / Alpha")
    page.get_by_role("button", name="Save category", exact=True).click()
    expect(page.get_by_text("Category saved.", exact=True)).to_be_visible()
    page.goto(base + "/add/")
    page.get_by_label("Name:", exact=True).fill(item)
    page.get_by_text("Sizes, packs and other details", exact=True).click()
    page.get_by_label("Category:", exact=True).select_option(label=root + " / Alpha / Beta")
    page.get_by_role("button", name="Save stock", exact=True).click()
    expect(page).to_have_url(re.compile(r"/products/\d+/$"))
    product_url = page.url
    expect(page.locator(".eyebrow")).to_contain_text(root + " / Alpha / Beta")
    check_stock_layouts(page, base, name, root, item)
    headings = page.locator(".category-heading .category-toggle")
    expect(headings).to_have_text([root, "Alpha", "Beta"])
    expect(page.get_by_role("heading", name="Beta", exact=True, level=4)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(ARTIFACTS / (name + "-category-tree.png")), full_page=True)
    page.get_by_role("link", name="Categories", exact=True).click()
    page.get_by_role("link", name="Delete " + root, exact=True).click()
    expect(page.get_by_text("This removes 3 categories.", exact=False)).to_be_visible()
    page.get_by_role("link", name="Cancel", exact=True).click()
    page.get_by_role("link", name="Delete " + root, exact=True).click()
    page.get_by_role("button", name="Delete category branch", exact=True).click()
    expect(
        page.get_by_text("Category branch deleted. Its items are now Uncategorised.")
    ).to_be_visible()
    page.goto(product_url)
    expect(page.locator(".eyebrow")).to_have_text("Uncategorised")
    expect(page.locator(".big-quantity")).to_contain_text("1")
    expect(page.get_by_role("link", name="Choose category", exact=True)).to_be_visible()
    page.get_by_role("link", name="Delete item permanently").click()
    page.get_by_role("button", name="Delete item", exact=True).click()
    expect(page.get_by_text("Item permanently deleted.")).to_be_visible()
    page.goto(base)


def main():
    engine = os.environ.get("PREPPERDB_BROWSER", "chromium")
    if engine not in ("chromium", "webkit"):
        raise ValueError("Unsupported acceptance browser.")
    with tempfile.TemporaryDirectory(prefix="prepperdb-browser-") as state:
        env = {
            **os.environ,
            "PREPPERDB_DEVELOPMENT": "1",
            "PREPPERDB_STATE_DIR": state,
            "DJANGO_SETTINGS_MODULE": "prepperdb.settings",
        }
        subprocess.run(
            [os.sys.executable, "manage.py", "migrate", "--noinput"], cwd=ROOT, env=env, check=True
        )
        # Synthetic credentials are disposable fixtures, never an application default.
        subprocess.run(
            [
                os.sys.executable,
                "manage.py",
                "shell",
                "-c",
                "from stock.models import User; User.objects.create_user('browser-fixture', 'synthetic-browser-password-9187')",
            ],
            cwd=ROOT,
            env=env,
            check=True,
        )
        subprocess.run(
            [
                os.sys.executable,
                "manage.py",
                "shell",
                "-c",
                """
import datetime,hashlib
from io import BytesIO
from PIL import Image
from django.utils import timezone
from stock.models import LookupCache,LookupResult,ProductImage
out=BytesIO(); Image.new('RGB',(40,40),'red').save(out,'JPEG'); data=out.getvalue()
image=ProductImage.objects.create(digest=hashlib.sha256(data).hexdigest(),data=data)
cache=LookupCache.objects.create(key='barcode:05449000000996',status='found',expires=timezone.now()+datetime.timedelta(days=1))
LookupResult.objects.create(cache=cache,code='5449000000996',image=image,details={'code':'5449000000996','name':'Cached online fixture','size_value':'400','size_unit':'g','packaging':'tin','category':'Food & drink / Tinned products','items_per_pack':1,'source_url':'https://world.openfoodfacts.org/product/5449000000996','quantity_label':'400 g'})
""",
            ],
            cwd=ROOT,
            env=env,
            check=True,
        )
        fixture_code = (ROOT / "stock/tests/fixtures/ean13.txt").read_text().strip()
        subprocess.run(
            [
                os.sys.executable,
                "manage.py",
                "shell",
                "-c",
                "import uuid; from stock.models import Product,Barcode; p=Product.objects.create(family=uuid.uuid4(),name='Saved barcode fixture',size_value=400,size_unit='g',packaging='tin'); Barcode.objects.create(product=p,code='"
                + fixture_code
                + "',items_per_pack=6)",
            ],
            cwd=ROOT,
            env=env,
            check=True,
        )
        picture = Image.open(ROOT / "stock/tests/fixtures/ean13.png").convert("YCbCr")
        y, cb, cr = picture.split()
        frame = y.tobytes() + cb.resize((320, 240)).tobytes() + cr.resize((320, 240)).tobytes()
        camera = Path(state) / "barcode.y4m"
        with camera.open("wb") as stream:
            stream.write(b"YUV4MPEG2 W640 H480 F10:1 Ip A1:1 C420jpeg\n")
            for _ in range(10):
                stream.write(b"FRAME\n" + frame)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        with (ARTIFACTS / "server.log").open("w") as log:
            server = subprocess.Popen(
                [
                    os.sys.executable,
                    "ci/offline_server.py",
                    "runserver",
                    f"127.0.0.1:{port}",
                    "--noreload",
                    "--insecure",
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                for _ in range(100):
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            break
                    except OSError:
                        if server.poll() is not None:
                            raise RuntimeError("Test server exited. See server.log.")
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Test server did not start.")
                with sync_playwright() as playwright:
                    browser = getattr(playwright, engine).launch(
                        args=[
                            "--use-fake-ui-for-media-stream",
                            "--use-fake-device-for-media-stream",
                            "--use-file-for-fake-video-capture=" + str(camera),
                        ]
                        if engine == "chromium"
                        else []
                    )
                    for name, viewport in [
                        (engine + "-desktop", {"width": 1280, "height": 900}),
                        (engine + "-phone", {"width": 390, "height": 844}),
                    ]:
                        context = browser.new_context(viewport=viewport)
                        external = []

                        def deny_wan(route):
                            if route.request.url.startswith((base + "/", "blob:" + base + "/")):
                                route.continue_()
                            else:
                                external.append(route.request.url)
                                route.abort()

                        context.route("**/*", deny_wan)
                        page = context.new_page()
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.on(
                            "console",
                            lambda message: (
                                print(f"Browser console [{message.type}]: {message.text}")
                                if message.type == "error"
                                else None
                            ),
                        )
                        page.goto(base)
                        assert_labels(page)
                        page.get_by_label("Username").focus()
                        page.keyboard.insert_text("browser-fixture")
                        page.keyboard.press("Tab")
                        expect(page.get_by_label("Password")).to_be_focused()
                        page.keyboard.insert_text("synthetic-browser-password-9187")
                        page.keyboard.press("Tab")
                        expect(
                            page.get_by_role("button", name="Sign in", exact=True)
                        ).to_be_focused()
                        page.keyboard.press("Enter")
                        expect(page.get_by_role("heading", name="Stock status")).to_be_visible()
                        check_categories(page, base, name)
                        check_adjustment_bounds(page, base, name)
                        check_attention_delete(page, base, name)
                        expect(page.locator("html")).to_have_attribute("data-theme", "dark")
                        page.get_by_role("button", name="Use light theme", exact=True).click()
                        page.reload()
                        expect(page.locator("html")).to_have_attribute("data-theme", "light")
                        page.get_by_role("button", name="Use dark theme", exact=True).click()
                        expect(
                            page.get_by_role("link", name="Add stock", exact=True)
                        ).to_have_count(1)
                        page.get_by_role("link", name="Add stock", exact=True).first.click()
                        page.get_by_label("Name:", exact=True).fill("Beans " + name)
                        quantity = page.get_by_label(
                            "Quantity (packs or individual items):", exact=True
                        )
                        page.get_by_role(
                            "button",
                            name="Increase Quantity (packs or individual items)",
                            exact=True,
                        ).click()
                        expect(quantity).to_have_value("2")
                        page.get_by_role(
                            "button",
                            name="Decrease Quantity (packs or individual items)",
                            exact=True,
                        ).click()
                        expect(quantity).to_have_value("1")
                        page.get_by_role(
                            "button",
                            name="Decrease Quantity (packs or individual items)",
                            exact=True,
                        ).click()
                        expect(quantity).to_have_value("1")
                        page.get_by_text("Sizes, packs and other details", exact=True).click()
                        assert_labels(page)
                        page.get_by_label("Items per pack:", exact=True).fill("6")
                        day = page.get_by_label("Day (optional for best before)", exact=True)
                        day.select_option("31")
                        page.get_by_label("Month", exact=True).select_option("2")
                        page.get_by_label("Year", exact=True).select_option("2028")
                        expect(day.locator('option[value="30"]')).to_be_disabled()
                        expect(day.locator('option[value="29"]')).to_be_enabled()
                        day.select_option("")
                        assert day.evaluate("element => element.validity.valid")
                        page.screenshot(path=str(ARTIFACTS / (name + "-entry.png")), full_page=True)
                        page.get_by_role("button", name="Save stock").click()
                        expect(page.locator(".big-quantity")).to_contain_text("6")
                        expect(
                            page.get_by_role("heading", name="Best before 02/2028")
                        ).to_be_visible()
                        page.get_by_role("button", name="Use one", exact=True).click()
                        expect(page.locator(".big-quantity")).to_contain_text("5")
                        product_url = page.url
                        page.get_by_role("link", name="History", exact=True).click()
                        page.locator(".history-entry").first.get_by_role(
                            "button", name="Undo this change"
                        ).click()
                        expect(page.locator(".history-entry").first).to_contain_text(
                            "Undone: 5 → 6"
                        )
                        page.goto(product_url)
                        expect(page.locator(".big-quantity")).to_contain_text("6")
                        check_item_views(page, base, name, "Beans " + name)
                        page.get_by_role("link", name="Endangered stock", exact=True).click()
                        expect(page.get_by_role("heading", name="Endangered stock")).to_be_visible()
                        page.screenshot(
                            path=str(ARTIFACTS / f"{name}-rotation.png"), full_page=True
                        )
                        page.get_by_role("link", name="Stock status", exact=True).first.click()
                        expect(
                            page.get_by_role("link", name="Add stock", exact=True)
                        ).to_have_count(1)
                        page.screenshot(path=str(ARTIFACTS / f"{name}-stock.png"), full_page=True)
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= innerWidth"
                        ), name + ": horizontal overflow"
                        # Every visible non-hidden form control has an explicit label or button text.
                        page.get_by_role("link", name="Needs attention").click()
                        expect(page.get_by_text("Missing:", exact=False).first).to_be_visible()
                        page.get_by_role("link", name="Add size", exact=True).filter(
                            visible=True
                        ).first.click()
                        expect(
                            page.get_by_role("heading", name="Correct product details")
                        ).to_be_visible()
                        expect(
                            page.get_by_label("Individual item size:", exact=True)
                        ).to_be_focused()
                        page.get_by_label("Individual item size:", exact=True).fill("400")
                        page.get_by_label("Size unit:", exact=True).select_option("g")
                        page.get_by_role("button", name="Save corrections", exact=True).click()
                        page.get_by_role("link", name="Add photo", exact=True).click()
                        page.get_by_label("Product photo:", exact=True).set_input_files(
                            str(ROOT / "stock/tests/fixtures/ean13.png")
                        )
                        expect(page.locator("canvas")).to_be_visible()
                        page.get_by_role("button", name="Save photo", exact=True).click()
                        expect(page.locator(".heading img")).to_have_js_property(
                            "naturalWidth", 800
                        )
                        check_photo_editor(page, base, name)
                        check_item_views(page, base, name, "Beans " + name, has_photo=True)
                        page.get_by_role("link", name="Correct date", exact=True).click()
                        page.get_by_label("Month", exact=True).select_option("3")
                        page.get_by_role("button", name="Save date", exact=True).click()
                        expect(page).to_have_url(re.compile(r"/products/\d+/$"))
                        expect(page.locator("article.batch")).to_have_count(1)
                        expect(
                            page.get_by_role("heading", name="Best before 03/2028")
                        ).to_be_visible()
                        page.get_by_role("link", name="Stock status", exact=True).first.click()
                        page.get_by_role("link", name="Add stock", exact=True).first.click()
                        page.get_by_label("Find a saved product or barcode").fill("Beans " + name)
                        expect(page.locator("#catalogue-status")).to_contain_text(
                            "Choose a saved product below."
                        )
                        page.get_by_role("link", name="Beans " + name, exact=False).first.click()
                        expect(page.get_by_label("Known size")).to_be_visible()
                        page.get_by_role("button", name="Save stock").click()
                        page.get_by_role("link", name="Delete item permanently").click()
                        expect(page.get_by_text("Are you sure", exact=False)).to_be_visible()
                        page.get_by_role("link", name="Cancel", exact=True).click()
                        expect(page.locator(".big-quantity")).to_be_visible()
                        page.get_by_role("link", name="Delete item permanently").click()
                        page.get_by_role("button", name="Delete item", exact=True).click()
                        expect(page.get_by_text("Item permanently deleted.")).to_be_visible()
                        page.get_by_role("link", name="Add stock", exact=True).first.click()
                        page.get_by_label("Scan or enter a barcode").fill("5449000000996")
                        page.get_by_role("button", name="Look up barcode", exact=True).click()
                        expect(page.get_by_label("Name:", exact=True)).to_have_value(
                            "Cached online fixture"
                        )
                        expect(page.locator(".lookup-preview img")).to_be_visible()
                        expect(page.locator(".lookup-preview img")).to_have_js_property(
                            "naturalWidth", 40
                        )
                        page.get_by_role("button", name="Save stock", exact=True).click()
                        expect(page.locator(".big-quantity")).to_contain_text("1")
                        page.get_by_role("link", name="Delete item permanently").click()
                        page.get_by_role("button", name="Delete item", exact=True).click()
                        page.get_by_role("button", name="Sign out").click()
                        expect(page.get_by_label("Password")).to_be_visible()
                        assert not external, external
                        assert not errors, errors
                        context.close()
                    # Real ZXing decoding of a photograph and a camera video stream,
                    # with every browser/server WAN connection still denied.
                    for mode in (
                        ("photo", "camera", "denied")
                        if engine == "chromium"
                        else ("photo", "denied")
                    ):
                        print("Checking barcode mode:", mode, flush=True)
                        context = browser.new_context(viewport={"width": 390, "height": 844})
                        external = []
                        context.route(
                            "**/*",
                            lambda route: (
                                route.continue_()
                                if route.request.url.startswith((base + "/", "blob:" + base + "/"))
                                else (external.append(route.request.url), route.abort())
                            ),
                        )
                        context.add_init_script("delete window.BarcodeDetector;")
                        if mode == "camera":
                            context.add_init_script("""const acquire = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
                                navigator.mediaDevices.getUserMedia = constraints => {
                                    window.cameraRequestedFromGesture = navigator.userActivation.isActive;
                                    return acquire(constraints);
                                };""")
                        if mode == "denied":
                            context.add_init_script(
                                "navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('Denied','NotAllowedError'); };"
                            )
                        page = context.new_page()

                        page.on(
                            "console",
                            lambda message: (
                                print("Scanner console:", message.text)
                                if message.type == "error"
                                else None
                            ),
                        )

                        def decoder_request(route):
                            if mode == "photo":
                                # Native phone pickers can hide the page while a
                                # selected photo is waiting for the local decoder.
                                page.evaluate("""() => {
                                    Object.defineProperty(document, 'hidden', {configurable:true, get:()=>true});
                                    document.dispatchEvent(new Event('visibilitychange'));
                                    delete document.hidden;
                                }""")
                            elif mode == "camera":
                                assert page.evaluate("window.cameraRequestedFromGesture === true")
                            route.continue_()

                        page.route("**/vendor/zxing-browser-*.js", decoder_request)
                        page.goto(base)
                        page.get_by_label("Username").fill("browser-fixture")
                        page.get_by_label("Password").fill("synthetic-browser-password-9187")
                        page.get_by_role("button", name="Sign in", exact=True).click()
                        page.get_by_role("link", name="Add stock", exact=True).first.click()
                        if mode == "photo":
                            page.locator("#barcode-photo").set_input_files(
                                str(ROOT / "stock/tests/fixtures/ean13.png")
                            )
                        else:
                            page.get_by_role("button", name="Scan barcode", exact=True).click()
                            if mode == "denied":
                                expect(page.locator("#scanner-status")).to_contain_text(
                                    "permission is blocked"
                                )
                                expect(page.locator("#camera-help")).to_have_attribute("open", "")
                                page.get_by_label("Scan or enter a barcode").fill(fixture_code)
                                page.get_by_role(
                                    "button", name="Look up barcode", exact=True
                                ).click()
                        expect(page.get_by_label("Known size")).to_be_visible(timeout=30000)
                        expect(page.get_by_label("Name:", exact=True)).to_have_value(
                            "Saved barcode fixture"
                        )
                        expect(page.get_by_label("Items per pack:", exact=True)).to_have_value("6")
                        if mode == "photo" and engine == "chromium":
                            # Also exercise the native-detector integration path.
                            page.get_by_role("link", name="Choose another product").click()
                            page.evaluate(
                                "code => { window.BarcodeDetector = class { static async getSupportedFormats(){ return ['ean_13']; } async detect(){ return [{rawValue:code}]; } }; }",
                                fixture_code,
                            )
                            page.get_by_role("button", name="Scan barcode", exact=True).click()
                            expect(page.get_by_label("Known size")).to_be_visible()
                        page.get_by_role("button", name="Save stock", exact=True).click()
                        page.screenshot(
                            path=str(ARTIFACTS / (engine + "-barcode-" + mode + ".png")),
                            full_page=True,
                        )
                        page.get_by_role("button", name="Sign out").click()
                        assert not external, external
                        context.close()
                    browser.close()
                print(
                    f"PASS ({engine}): desktop/phone stock, themes, contextual entry, real barcode photo decoding, denied-camera fallback and offline saved-product lookup; no WAN requests. Chromium also checks camera video and native integration."
                )
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == "__main__":
    main()
