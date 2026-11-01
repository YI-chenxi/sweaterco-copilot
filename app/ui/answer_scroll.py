"""One-shot scrolling limited to the owned conversation container."""

import json


def consume_scroll_target(thread):
    identity = thread.pop("scroll_to_answer", None)
    if identity and any(m.get("ui_reply_id") == identity for m in thread["messages"]):
        return identity
    return None


def scroll_script(identity):
    # JSON quoting keeps identifiers data; no response text enters executable HTML.
    target = json.dumps("sc-answer-" + identity).replace("<", "\\u003c")
    return """<script>(() => {
      const targetId = TARGET;
      let frames = 0, previous = -1, stable = 0;
      const attempt = () => {
        const region = document.querySelector('.st-key-conversation_scroll');
        const target = document.getElementById(targetId);
        if (region && target && region.contains(target)) {
          const top = region.scrollTop + target.getBoundingClientRect().top
                    - region.getBoundingClientRect().top - 12;
          stable = Math.abs(top - previous) < 1 ? stable + 1 : 0;
          previous = top;
          if (stable >= 4) {
            region.scrollTo({top: Math.max(0, top), behavior: 'instant'});
            return;
          }
        }
        if (++frames < 120) requestAnimationFrame(attempt);
      };
      requestAnimationFrame(attempt);
    })();</script>""".replace("TARGET", target)
