# M4.3 Guarded DGM Promotion Full Gate Report

- Run dir: `/Users/shixiangweii/PycharmProjects/2026_qoder_proj/agent_loop_demo/评测/2026-6-12-04-m4.3-local`
- Project root: `/Users/shixiangweii/PycharmProjects/2026_qoder_proj/agent_loop_demo`
- Started at: `2026-06-12T19:02:58`
- Overall: PASS
- Secret scan: PASS
- API key: runtime environment only; not written to artifacts

| Check | Result | Details |
|---|---:|---|
| offline_pytest | PASS | rc=0; output=`/Users/shixiangweii/PycharmProjects/2026_qoder_proj/agent_loop_demo/评测/2026-6-12-04-m4.3-local/pytest-output.txt` |
| basic_eval_real_model | PASS | skipped: missing MU_MODEL, MU_API_KEY or OPENAI_API_KEY |
| dgm_lite_fake_agent_smoke | PASS | 3/3 tasks; archive=`/Users/shixiangweii/PycharmProjects/2026_qoder_proj/agent_loop_demo/评测/2026-6-12-04-m4.3-local/dgm-smoke/archive/archive.jsonl` |
| metatool_fake_model_smoke | PASS | inner_bash_events=1; permission_denied=True; spec=`/Users/shixiangweii/PycharmProjects/2026_qoder_proj/agent_loop_demo/评测/2026-6-12-04-m4.3-local/metatool-smoke/metatools/quick_pytest.json` |
| dgm_promotion_smoke | PASS | dirty_rejected=True; applied=True; patch=`/Users/shixiangweii/PycharmProjects/2026_qoder_proj/agent_loop_demo/评测/2026-6-12-04-m4.3-local/dgm-promotion-smoke/archive/promotions/20260612-190317-554185-m4.3-promotion-20260612-190317-504130/promotion.patch` |
