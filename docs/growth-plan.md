# How to make baton's repo grow: a launch plan

> Written 2026-09-23 on `mobile-branch`, to keep for later.
> **Question asked:** what do I have to do so my GitHub repo blows up the way
> OpenClaw's did?

**Honest framing:** there is no reliable recipe. Projects that grow like
OpenClaw (reported at ~386,000 stars by August 2026) are rare, and luck and
timing play a big part. But the ones that do grow share a clear set of traits,
and most of them can be built deliberately.

---

## 1. What repos that blow up have in common

1. **Anyone sees the value in 10 seconds.** A GIF or short video at the top of
   the README shows something surprising happening. OpenClaw's was "my AI did
   real things on my computer, from WhatsApp."
2. **It's useful within minutes.** One install command, and it works on the
   first try without configuration.
3. **It solves a pain people already feel.** Nobody searches for "routing
   kernels", but everyone who has built agents has been burned by one saying
   "done" when it wasn't.
4. **It works with what people already use.** Any model, any framework. No
   switching required.
5. **Distribution on launch day.** Hacker News ("Show HN"), Reddit
   (r/LocalLLaMA, r/MachineLearning, r/Python), X/Twitter and LinkedIn, on the
   same day, with a strong headline.
6. **Timing.** It rides a wave people are already excited about. Right now that
   is AI agents, so baton is in the right space.
7. **Fast response after launch.** Answering issues within hours, merging
   contributions, shipping fixes daily in the first weeks. Momentum compounds.

## 2. Where baton stands against that list

| trait | baton today |
|---|---|
| value in 10 seconds | ❌ its value is invisible (it prevents problems) |
| useful in minutes | ⚠️ `examples/triage.py` runs offline, but it's not on PyPI and the repo is private |
| felt pain | ✅ silent failures, fake "done", runaway agent loops |
| works with what people use | ⚠️ `Dispatch` makes it possible, but there are no adapters yet |
| distribution | ❌ not launched |
| a unique story | ✅ the honest failure write-up is rare and very shareable |

## 3. The plan

### Step 1: change the pitch

Pitch it as **"your agents can't lie about being done"**, not "a routing
kernel". The routing is interesting to the author; the safety net is what
others need. A possible headline:

> *"I caught my AI agents claiming 'done' on 37% of tasks where nothing was
> delivered. So I built a gate they can't fool."*

The 37% figure is from batch 3 in `docs/measuring-dynamic-routing.md`: 33 of
90 real runs were ratified by an LLM gate with nothing delivered.

### Step 2: build the demo that makes people react

- A normal OpenAI Agents SDK or LangGraph agent reports success on empty work.
- Wrap it with baton in three lines; the fake "done" is caught, with a named
  reason.
- Record it as a 20-second GIF and put it at the top of the README.

### Step 3: ship `verify()` and one adapter

This is `docs/contracts-design.md`. People don't switch frameworks; they add
tools to the one they have. "Add this to your existing agent" gets far more
users than "rewrite with my framework."

### Step 4: make trying it effortless

- Make the repo public again.
- Publish on PyPI, so `pip install baton-kernel` works.
- Put the GIF, one example and the install line at the top of the README.
- Keep "zero dependencies" in the first three lines; developers love it.

### Step 5: launch everywhere on the same day, using the real story

Hacker News responds very well to honest engineering stories.
`docs/measuring-dynamic-routing.md` ("We tried to measure dynamic routing four
times. Here is how each one failed.") is exactly the kind of post that reaches
the HN front page. Lead with that; the library is the follow-up. Post to Reddit,
X and LinkedIn the same day.

### Step 6: be present for the first two weeks

Reply to every comment and issue, and ship fixes quickly. Most projects die at
this stage from silence, not from bad code.

## 4. Realistic expectations

- **A great launch of a developer tool** usually means hundreds to a few
  thousand stars. That is a real success.
- **OpenClaw-scale growth** came from being an *end-user product*. If that is
  the goal, build a product on top of baton: an agent people use directly,
  whose selling point is that it never says it's done without proof.
- **Stars aren't users.** The signs of real success are people filing issues,
  asking questions and sending code.

**Strongest asset:** it isn't the code. It's the habit of measuring honestly
and publishing failures, which very few people in AI do. Lead with it.

## 5. Checklist

- [ ] Repo public again
- [ ] Published on PyPI
- [ ] `baton.contracts` + `verify()` implemented (see `docs/contracts-design.md`)
- [ ] One adapter (OpenAI Agents SDK or LangGraph)
- [ ] 20-second "caught a fake done" GIF at the top of the README
- [ ] One live, real-model result
- [ ] Launch post drafted (HN: the measurement story; LinkedIn: see
      `docs/patterns-landscape.md` §6)
- [ ] Two weeks set aside for replying to issues after launch
