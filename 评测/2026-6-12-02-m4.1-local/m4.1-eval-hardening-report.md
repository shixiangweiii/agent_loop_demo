# M4.1 Eval Hardening Full Gate Report

- Run dir: `/Users/shixiangweii/PycharmProjects/2026_qoder_proj/agent_loop_demo/评测/2026-6-12-02-m4.1-local`
- Project root: `/Users/shixiangweii/PycharmProjects/2026_qoder_proj/agent_loop_demo`
- Started at: `2026-06-12T14:23:33`
- Overall: PASS
- Secret scan: PASS
- API key: runtime environment only; not written to artifacts

| Check | Result | Details |
|---|---:|---|
| offline_pytest | PASS | rc=0; output=`/Users/shixiangweii/PycharmProjects/2026_qoder_proj/agent_loop_demo/评测/2026-6-12-02-m4.1-local/pytest-output.txt` |
| basic_eval_real_model | PASS | skipped: missing MU_MODEL, MU_API_KEY or OPENAI_API_KEY |
| dgm_lite_fake_agent_smoke | PASS | 3/3 tasks; archive=`/Users/shixiangweii/PycharmProjects/2026_qoder_proj/agent_loop_demo/评测/2026-6-12-02-m4.1-local/dgm-smoke/archive/archive.jsonl` |
