"""W1 DOM recon helper — opens a real e-tax session with a tarry() console helper.

Usage:
    uv run python scripts/recon.py --tax-id 91310000XXXXXXXXXX

What this does:
  - Launches system Chrome via tax_auto.core.browser (persistent context per tax_id)
  - Injects a JS helper that adds `window.tarry` to every page
  - You drive the 7-step flow manually in the GUI
  - In DevTools console, run:
        tarry()                          # dump candidate selectors of last-clicked element
        tarry.save('step_3_date_from')   # also save screenshot + HTML to runtime/recon/
        tarry.snapshot('query_page')     # save the WHOLE page (for W1.5 fixtures)
        tarry.check_session()            # ping landing URL, log alive/expired (W1.3)
  - Output goes to runtime/recon/  (gitignored under runtime/)

The script blocks until you close the browser window.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from tax_auto.config import get_settings
from tax_auto.core.browser import make_persistent_context
from tax_auto.core.session import LOGIN_URL


# ── The injected helper. addInitScript runs this on EVERY page/frame load. ──
# Keep it self-contained, no external deps.
_TARRY_HELPER_JS = r"""
// ──────── Anti-anti-debug ────────
// 电子税务局用 new Function('debugger') / setTimeout('debugger', N) 死循环卡 DevTools。
// 拦截所有可能创建 `debugger` 关键字的路径,把它剥成注释。
// addInitScript 保证这段在页面所有脚本之前执行。
//
// 已知的绕过路径,全部覆盖:
//   - new Function('debugger')()                            → ✓ proxy on window.Function
//   - (function(){}).constructor('debugger')()              → ✓ patch Function.prototype.constructor
//   - ({}).toString.constructor.constructor('debugger')()   → ✓ 同上
//   - eval('debugger')                                      → ✓ proxy on window.eval
//   - setTimeout('debugger', 100) / setInterval('debugger') → ✓ proxy on global timers
// 硬编码在 JS 源码里的 `debugger;` 关键字依赖 V8 层 setSkipAllPauses(Python 端发 CDP)。
(() => {
  try {
    const stripDbg = (s) =>
      typeof s === 'string' ? s.replace(/\bdebugger\b/g, '/*dbg*/') : s;
    const OrigFn = window.Function;

    const ProxyFn = new Proxy(OrigFn, {
      construct(target, args) {
        return new target(...args.map(stripDbg));
      },
      apply(target, thisArg, args) {
        return target.apply(thisArg, args.map(stripDbg));
      },
    });
    window.Function = ProxyFn;

    // 关键:让 Function.prototype.constructor 也指向代理,堵住
    //   (function(){}).constructor('debugger')() 这种绕过
    try {
      Object.defineProperty(OrigFn.prototype, 'constructor', {
        value: ProxyFn,
        writable: true,
        configurable: true,
      });
    } catch (e) { /* 极少数严格模式或冻结对象,无视 */ }

    // eval('debugger')
    const origEval = window.eval;
    window.eval = function (s) { return origEval(stripDbg(s)); };

    // setTimeout('debugger', N) / setInterval('debugger', N)
    const origST = window.setTimeout;
    window.setTimeout = function (handler, timeout, ...rest) {
      if (typeof handler === 'string') handler = stripDbg(handler);
      return origST.call(this, handler, timeout, ...rest);
    };
    const origSI = window.setInterval;
    window.setInterval = function (handler, timeout, ...rest) {
      if (typeof handler === 'string') handler = stripDbg(handler);
      return origSI.call(this, handler, timeout, ...rest);
    };
  } catch (e) {
    /* swallow — 不要让我们的代理本身把页面崩了 */
  }
})();

(() => {
  if (window.__tarryReady) return;

  // Record last-clicked element via capture-phase listener
  window.__tarryLastEl = null;
  document.addEventListener('click', (e) => {
    window.__tarryLastEl = e.target;
  }, { capture: true });

  // Filter classnames that look stateful (Element-UI etc.)
  const STATEFUL = /^(is-|el-popper|active|hover|focus|disabled|focused|selected)/;
  const trimClass = (c) =>
    (c || '').split(/\s+/).filter((x) => x && !STATEFUL.test(x));

  const escAttr = (v) => String(v).replace(/"/g, '\\"').slice(0, 80);

  function dumpEl(el) {
    if (!el) return { error: 'no element — click something first' };
    const tag = (el.tagName || 'unknown').toLowerCase();
    const text = (el.innerText || el.value || '').trim().slice(0, 60);
    const role = el.getAttribute('role');
    const name = el.getAttribute('name');
    const ariaLabel = el.getAttribute('aria-label');
    const placeholder = el.getAttribute('placeholder');
    const id = el.id;

    const tier1 = [];
    if (id) tier1.push('#' + id);
    if (name) tier1.push(`[name="${escAttr(name)}"]`);
    if (ariaLabel) tier1.push(`[aria-label="${escAttr(ariaLabel)}"]`);
    if (placeholder) tier1.push(`${tag}[placeholder="${escAttr(placeholder)}"]`);

    const tier2 = [];
    if (text) tier2.push(`text="${escAttr(text)}"`);
    if (role && text) tier2.push(`role=${role}[name="${escAttr(text)}"]`);
    else if (role) tier2.push(`role=${role}`);
    if (text) tier2.push(`${tag}:has-text("${escAttr(text)}")`);

    const classes = trimClass(el.className);
    const tier3 = classes.length
      ? [`${tag}.${classes.join('.')}`]
      : [];

    return {
      tag, text, role, id, name,
      ariaLabel, placeholder,
      tier1, tier2, tier3,
    };
  }

  // Public API
  window.tarry = function () {
    const out = dumpEl(window.__tarryLastEl);
    console.log('--- tarry recon ---');
    console.log(JSON.stringify(out, null, 2));
    return out;
  };

  // CRITICAL: keep CDP payloads SMALL. Passing the entire outerHTML
  // through a binding crashed the Node driver on SPA-heavy pages
  // (multi-MB stringify → V8 OOM). Python side now pulls HTML/screenshot
  // via separate Playwright API calls, which are streamed properly.
  window.tarry.save = async function (label) {
    try {
      if (!label || !/^[\w一-鿿\-]+$/.test(label)) {
        console.error('tarry.save: label must be ASCII word chars / Chinese / -, got', label);
        return;
      }
      if (typeof window.__tarrySave !== 'function') {
        console.error('tarry.save: Playwright binding __tarrySave missing on this page!');
        return;
      }
      const dump = dumpEl(window.__tarryLastEl);
      if (dump.error) {
        console.warn('tarry.save: no element selected — click something first');
      }
      // Tiny payload: ~1KB. No HTML.
      await window.__tarrySave({ label, dump, mode: 'save' });
      console.log(`✓ saved runtime/recon/${label}.{png,html,md}  (from ${location.href})`);
    } catch (e) {
      console.error('tarry.save FAILED:', e && e.message || e);
    }
  };

  window.tarry.snapshot = async function (label) {
    try {
      if (!label || !/^[\w一-鿿\-]+$/.test(label)) {
        console.error('tarry.snapshot: label must be ASCII word chars / Chinese / -');
        return;
      }
      if (typeof window.__tarrySave !== 'function') {
        console.error('tarry.snapshot: binding missing on this page');
        return;
      }
      // No payload other than label — Python pulls HTML.
      await window.__tarrySave({ label, dump: null, mode: 'snapshot' });
      console.log(`✓ snapshot runtime/recon/${label}.{png,html}  (from ${location.href})`);
    } catch (e) {
      console.error('tarry.snapshot FAILED:', e && e.message || e);
    }
  };

  window.tarry.check_session = async function () {
    try {
      const result = await window.__tarryCheckSession();
      console.log(result.alive ? '✓ session alive' : '✗ session EXPIRED', result);
      return result;
    } catch (e) {
      console.error('tarry.check_session FAILED:', e && e.message || e);
    }
  };

  window.__tarryReady = true;
  console.log(`🔍 tarry recon ready on ${location.href}`);
  console.log('   tarry.save("step_3_date_from")  → screenshot + HTML + selector dump');
  console.log('   tarry.snapshot("query_page")    → save whole page (W1.5 fixtures)');
  console.log('   tarry.check_session()           → log alive/expired (W1.3)');
})();
"""


def _ensure_recon_dir() -> Path:
    d = get_settings().runtime_dir / "recon"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_artifact(label: str, dump: dict | None, html: str, url: str,
                    screenshot_bytes: bytes, snapshot_only: bool) -> None:
    recon = _ensure_recon_dir()
    (recon / f"{label}.html").write_text(html, encoding="utf-8")
    (recon / f"{label}.png").write_bytes(screenshot_bytes)
    if snapshot_only or dump is None:
        return

    # Render a tidy markdown record next to the artifacts
    import json
    md = [
        f"# {label}",
        "",
        f"- url: `{url}`",
        f"- tag: `{dump.get('tag')}`",
        f"- text: `{dump.get('text')!r}`",
        f"- role: `{dump.get('role')}` · id: `{dump.get('id')}` · name: `{dump.get('name')}`",
        f"- aria-label: `{dump.get('ariaLabel')}` · placeholder: `{dump.get('placeholder')}`",
        "",
        "## Tier 1 (stable)",
        "```",
        *(dump.get("tier1") or ["(none)"]),
        "```",
        "## Tier 2 (semantic)",
        "```",
        *(dump.get("tier2") or ["(none)"]),
        "```",
        "## Tier 3 (brittle)",
        "```",
        *(dump.get("tier3") or ["(none)"]),
        "```",
        "",
        "## Raw dump",
        "```json",
        json.dumps(dump, ensure_ascii=False, indent=2),
        "```",
    ]
    (recon / f"{label}.md").write_text("\n".join(md), encoding="utf-8")


def _check_session(page_url: str) -> dict:  # type: ignore[type-arg]
    """Heuristic same as core.session.is_session_alive: if URL is on tpass.*, we're logged out."""
    alive = "tpass.shanghai.chinatax.gov.cn" not in (page_url or "")
    log = _ensure_recon_dir() / "session_lifetime_log.txt"
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    log.write_text(
        (log.read_text() if log.exists() else "")
        + f"{ts}  alive={alive}  url={page_url}\n",
        encoding="utf-8",
    )
    return {"alive": alive, "url": page_url, "ts": ts}


def main() -> int:
    parser = argparse.ArgumentParser(description="W1 DOM recon companion")
    parser.add_argument("--tax-id", required=True, help="统一社会信用代码")
    parser.add_argument("--landing-url", default=LOGIN_URL,
                        help=f"start URL (default: {LOGIN_URL})")
    args = parser.parse_args()

    _ensure_recon_dir()
    print(f"recon output dir: {get_settings().runtime_dir / 'recon'}")
    print("Press Ctrl-C in this terminal, OR close the browser window, to exit.")

    with sync_playwright() as p:
        with make_persistent_context(
            p, args.tax_id, headless=False,
            # Auto-open DevTools for EVERY tab/window. E-tax bureau opens
            # 我要办税 etc. in popup windows; this ensures the console is
            # already open there so user can run tarry.save() immediately.
            extra_args=["--auto-open-devtools-for-tabs"],
        ) as ctx:
            # 1. Inject tarry() helper on every navigation
            ctx.add_init_script(_TARRY_HELPER_JS)

            # 2. Set up Python-side bindings so tarry.save()/check_session() can reach disk
            _MAX_HTML_BYTES = 5_000_000  # 5 MB safety cap on stored HTML

            def _save_binding(source, payload):  # type: ignore[no-untyped-def]
                """Receive a tiny payload from JS, then pull HTML + screenshot
                via Playwright API directly (avoids passing megabytes through
                the CDP binding channel — that crashed the Node driver before).
                """
                try:
                    pg = source["page"]
                    label = payload["label"]
                    dump = payload.get("dump")
                    mode = payload.get("mode", "save")
                    snapshot_only = mode == "snapshot"
                    url = pg.url

                    # Pull HTML via dedicated CDP roundtrip (streamed by Playwright)
                    try:
                        html = pg.content()
                    except Exception as e:  # noqa: BLE001
                        html = f"<!-- page.content() failed: {e} -->"
                    if len(html) > _MAX_HTML_BYTES:
                        html = (
                            html[:_MAX_HTML_BYTES]
                            + f"\n<!-- truncated from {len(html)} bytes -->"
                        )

                    # Pull screenshot the same way (own CDP roundtrip)
                    try:
                        shot = pg.screenshot(full_page=False)
                    except Exception as e:  # noqa: BLE001
                        print(f"  [warn] screenshot failed for {label}: {e}")
                        shot = b""

                    _write_artifact(label, dump, html, url, shot, snapshot_only)
                    print(f"  ✓ artifact saved: {label} (mode={mode})")
                    return True
                except Exception as e:  # noqa: BLE001 — never propagate to driver
                    print(f"  [error] _save_binding crashed: {type(e).__name__}: {e}")
                    return False

            def _check_session_binding(source, _payload=None):  # type: ignore[no-untyped-def]
                pg = source["page"]
                return _check_session(pg.url)

            ctx.expose_binding("__tarrySave", _save_binding)
            ctx.expose_binding("__tarryCheckSession", _check_session_binding)

            # 3. Arm CDP setSkipAllPauses on EVERY page (initial + popups).
            #    Each page gets ONE CDP session (not per-frame — that crashed
            #    the Node driver previously with iframe-heavy SPAs).
            cdp_sessions: dict[str, object] = {}

            def _attach_anti_debug(pg) -> None:  # type: ignore[no-untyped-def]
                key = str(id(pg))
                if key in cdp_sessions:
                    return
                try:
                    cdp = ctx.new_cdp_session(pg)
                    cdp.send("Debugger.enable")
                    cdp.send("Debugger.setSkipAllPauses", {"skip": True})
                    cdp_sessions[key] = cdp
                    print(f"  ✓ V8 setSkipAllPauses armed on {pg.url or '(blank)'}")
                except Exception as e:  # noqa: BLE001
                    print(f"  [warn] CDP arm failed for {pg.url}: {e}")

                # Re-arm on page load (not per-frame!) in case the flag was reset.
                def _rearm() -> None:
                    cdp = cdp_sessions.get(key)
                    if cdp is None:
                        return
                    try:
                        cdp.send("Debugger.setSkipAllPauses", {"skip": True})
                    except Exception:  # noqa: BLE001
                        pass

                pg.on("load", lambda: _rearm())

            # 4. Watch for popups / new tabs opened by the site (e.g. 我要办税)
            def _on_new_page(pg) -> None:  # type: ignore[no-untyped-def]
                print(f"\n→ NEW PAGE opened: {pg.url or '(blank, navigating...)'}")
                print("  DevTools should auto-open here; tarry helper auto-injects.")
                _attach_anti_debug(pg)

            ctx.on("page", _on_new_page)

            # 5. Open landing page
            page = ctx.new_page()
            _attach_anti_debug(page)
            page.goto(args.landing_url)

            print("\n→ Browser is open. Walk the 7-step flow.")
            print("→ Open DevTools (Cmd-Opt-I), then in the Console try:")
            print("     tarry()")
            print("     tarry.save('step_3_input_date_from')")
            print("     tarry.snapshot('query_result_page')")
            print("     tarry.check_session()")
            print("\nWaiting for the browser to be closed...\n")

            # Block until the user closes the page/window
            try:
                page.wait_for_event("close", timeout=0)
            except KeyboardInterrupt:
                print("\nCtrl-C received, closing browser.")
    print("✓ recon session ended.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
