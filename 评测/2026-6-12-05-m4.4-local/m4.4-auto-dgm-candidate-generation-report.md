# M4.4 Auto DGM Candidate Generation Full Gate Report

- Run dir: `/Users/shixiangweii/run_proj/agent_loop_demo/评测/2026-6-12-05-m4.4-local`
- Project root: `/Users/shixiangweii/run_proj/agent_loop_demo`
- Started at: `2026-06-12T21:45:53`
- Overall: PASS
- Secret scan: PASS
- API key: runtime environment only; not written to artifacts

| Check | Result | Details |
|---|---:|---|
| offline_pytest | PASS | rc=0; output=`/Users/shixiangweii/run_proj/agent_loop_demo/评测/2026-6-12-05-m4.4-local/pytest-output.txt` |
| basic_eval_real_model | PASS | skipped: missing MU_MODEL, MU_API_KEY or OPENAI_API_KEY |
| dgm_lite_fake_agent_smoke | PASS | 3/3 tasks; archive=`/Users/shixiangweii/run_proj/agent_loop_demo/评测/2026-6-12-05-m4.4-local/dgm-smoke/archive/archive.jsonl` |
| dgm_auto_fake_agent_smoke | PASS | generated=2; passed=2; best=`auto-20260612-214630-218832-02-m4-4-auto-smoke`; summary=`/Users/shixiangweii/run_proj/agent_loop_demo/评测/2026-6-12-05-m4.4-local/dgm-auto-smoke/archive/auto-runs/auto-20260612-214630-218832-m4-4-auto-smoke/summary.json` |
| metatool_fake_model_smoke | PASS | inner_bash_events=1; permission_denied=True; spec=`/Users/shixiangweii/run_proj/agent_loop_demo/评测/2026-6-12-05-m4.4-local/metatool-smoke/metatools/quick_pytest.json` |
| dgm_promotion_smoke | PASS | dirty_rejected=True; applied=True; patch=`/Users/shixiangweii/run_proj/agent_loop_demo/评测/2026-6-12-05-m4.4-local/dgm-promotion-smoke/archive/promotions/20260612-214635-019525-m4.3-promotion-20260612-214634-936052/promotion.patch` |
