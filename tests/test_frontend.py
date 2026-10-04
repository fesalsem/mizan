"""
Structural guards for the frontend stylesheet.

There is no browser in CI, so these read index.html directly. That makes them
narrow by design: each one pins a specific defect that shipped, so that
reapplying the same edit fails a test rather than a user noticing it.

The defect behind the first test: a blanket `prefers-reduced-motion` rule set
`animation-duration:0.001ms` and `animation-iteration-count:1` on `*`. The
loading ring runs `animation:spin 0.7s linear infinite` for the whole request
and there is no other activity indicator, so for anyone with reduce-motion
enabled the ring stopped rotating and sat as a motionless circle. It reads as a
frozen page. Only slow requests made it visible, which is why it surfaced on US
tickers rather than on Bursa codes.
"""

import re
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parents[1] / "index.html"


def _css() -> str:
    html = INDEX.read_text(encoding="utf-8")
    match = re.search(r"<style[^>]*>(.*?)</style>", html, re.S)
    assert match, "no <style> block found in index.html"
    return match.group(1)


def _at_rule_body(css: str, preamble: str) -> str:
    """Return the brace-balanced body of the first at-rule matching preamble."""
    start = css.find(preamble)
    assert start != -1, f"{preamble!r} not found"
    open_brace = css.index("{", start)
    depth = 0
    for i in range(open_brace, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[open_brace + 1 : i]
    raise AssertionError(f"unbalanced braces after {preamble!r}")


def _declarations(body: str, selector: str) -> str:
    """Return the declaration block for one selector, ignoring nesting."""
    match = re.search(re.escape(selector) + r"\s*\{([^{}]*)\}", body)
    assert match, f"no rule for {selector!r} in the reduced-motion block"
    return match.group(1)


@pytest.fixture(scope="module")
def reduced_motion() -> str:
    return _at_rule_body(_css(), "@media (prefers-reduced-motion: reduce)")


class TestLoaderSurvivesReducedMotion:
    def test_ring_still_animates(self, reduced_motion):
        # The whole point: after the blanket reset, the ring must be given its
        # animation back. Without this the spinner is inert.
        decls = _declarations(reduced_motion, ".loader-ring")
        assert "infinite" in decls, (
            "The reduced-motion block leaves .loader-ring without an "
            "infinite animation, so the loading spinner cannot spin. Users "
            "with reduce-motion enabled see a frozen circle for the whole "
            "request and read it as a hang."
        )

    def test_ring_does_not_rotate(self, reduced_motion):
        # The fix must not simply restore `spin`, which is the movement the
        # preference asked us to avoid.
        decls = _declarations(reduced_motion, ".loader-ring")
        assert "spin" not in decls, (
            "The reduced-motion override restores the rotation instead of "
            "substituting a non-moving indicator, which defeats the setting."
        )

    def test_substitute_keyframes_exist(self):
        assert "@keyframes pulse" in _css(), (
            ".loader-ring references @keyframes pulse under reduced motion, "
            "but the keyframes are not defined."
        )

    def test_blanket_reset_is_still_there(self, reduced_motion):
        # Guard the other direction: the override must not have replaced the
        # blanket rule, which is what stops decorative movement.
        assert "animation-duration:0.001ms!important" in reduced_motion
        assert "animation-iteration-count:1!important" in reduced_motion

    def test_override_wins_on_specificity(self, reduced_motion):
        # The blanket rule uses !important on *, so the override needs
        # !important too, and .loader-ring must outrank * on specificity.
        decls = _declarations(reduced_motion, ".loader-ring")
        assert decls.count("!important") >= 3, (
            "The .loader-ring override must set its properties with "
            "!important to outrank the !important blanket reset."
        )
