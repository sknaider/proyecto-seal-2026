# Recuperación del reset --hard de las 13:27 (3-sep-2026) — mapa blob→archivo

**Qué pasó (medido por ADA):** `git reset --hard HEAD~1` a las 13:27:11 en la rama `nexus/end-session-runtime-role-20260829` reescribió **869 archivos** del árbol de trabajo (mtime 13:26:44-46). Justo antes (13:26:3x) alguien hizo `git add` de todo: los contenidos perdidos quedaron como **objetos sueltos** en `.git/objects` sin nombre. Este mapa los asocia a su archivo por similitud de contenido.

**Cómo restaurar uno (revisar el diff ANTES):**
```
git cat-file -p <blob> | diff - <ruta>      # ver qué recupera
git cat-file -p <blob> > <ruta>              # restaurar en disco
```

**SERVICIOS ACTIVOS QUE CORREN CÓDIGO QUE YA NO ESTÁ EN DISCO (arrancados 2-sep 11:51, archivo pisado 3-sep 13:26). NO REINICIAR sin restaurar antes:**

- `ada-codex-remote-bridge.service  messages/ada_codex_remote_bridge.py  (SIN blob: irrecuperable desde git, ver pyc)`
- `seal-ada-codex-poller.service  messages/ada_codex_poller.py`
- `seal-alice-dm-poller.service  messages/alice_dm_poller.py`
- `seal-jarvis-dm-poller.service  messages/jarvis_dm_poller.py`
- `seal-auto-compact.service  memory/auto_compact_watchdog.py`
- `seal-dum-watchdog.service  messages/dum_watchdog.py`
- `seal-soul-dashboard-api.service  soul-dashboard/soul_api.py`
- `seal-whisper-{ADA,ALICE,DUM,JARVIS,NEXUS}.service  messages/whisper_daemon.py`

Además `memory/mcp_server_v4.py` (+426 líneas perdidas) es el MCP seal-memory: verificar qué carga el proceso antes de cualquier restart.


## Código con versión perdida recuperable (241 archivos; ratio = similitud disco↔blob)

| Δ líneas | ratio | archivo | blob |
|---:|---:|---|---|
| +1993 | 0.94 | `seal-desktop/debian/seal-companion/usr/share/seal-companion/companion_core/main.py` | `2f0d6513da0d43c75b90c390f0bbf9dab79960e1` |
| +954 | 0.858 | `scripts/tests/test_ada_listening_healthcheck.py` | `64fe2f9e687aa7ba5b1d393f72fd4fe121dbcc33` |
| +728 | 0.873 | `soul-dashboard/soul_api.py` | `75e9f4d8310fafbac7084a02425877d79065003e` |
| +490 | 0.958 | `soul-platform/src/soul_platform/mcp_stdio.py` | `6406539eb215cb08d634250b3969aac338a16ef2` |
| +426 | 0.999 | `memory/mcp_server_v4.py` | `9dee0111dfb7b8e46decbd4daec65c5e6a9fc459` |
| +422 | 0.905 | `seal-desktop/companion_core/ui/src/views/ChatView.tsx` | `37a49540e58b3ad37c1c20c0e8e87f9487ddff90` |
| +340 | 0.93 | `memory/nerves_native_agent_receipt.py` | `65b2d971549bc8b519b31519e2b349ebf2793e0c` |
| +332 | 0.991 | `memory/test_nerves_native_agent_receipt.py` | `6247d2c8f1c686218e945c70e71520bf1ece1b46` |
| +263 | 0.947 | `memory/minisoul_sync_daemon.py` | `45fb1e17b36be9dbc927e19fb9142673e728bb14` |
| +214 | 0.973 | `messages/ada_codex_poller.py` | `15934a4b6baaf94b583c0f32b9be3667ea4fcaf6` |
| +191 | 0.942 | `memory/consolidate.py` | `4b33129fcc0664b909db030e0f3732f2622e75fa` |
| +188 | 0.964 | `tools/seal_dm.py` | `1869c008c57406b599f3e8e1a52c78ad41e7b426` |
| +151 | 0.73 | `seal-desktop/companion_core/ui/src/views/AIBackendView.tsx` | `0e156eaff90950ed28ff8d9c67229bf9c17ad564` |
| +150 | 0.698 | `seal-desktop/companion_core/ui/src/views/MemoryView.tsx` | `d85b410de36cd1cfbeca27bb7524c79734c0f4a8` |
| +144 | 0.742 | `seal-desktop/companion_core/companion_core/agent.py` | `6c767950478e42f2be59c39baa8d1ada6bf83c43` |
| +144 | 0.742 | `seal-desktop/debian/seal-companion/usr/share/seal-companion/companion_core/agent.py` | `6c767950478e42f2be59c39baa8d1ada6bf83c43` |
| +140 | 0.69 | `messages/seal_context_meter.py` | `50ecc3e8e5b7d4e7c4d3243dd5d8648de68ea76a` |
| +137 | 0.981 | `messages/tests/test_ada_codex_poller_context.py` | `ffa7d464063a19ed073fa5c934b478ed70ad8447` |
| +129 | 0.557 | `memory/auto_compact_watchdog.py` | `e459915515a148bcde85bfdeca140f9aff9843ea` |
| +128 | 0.532 | `tests/test_seal_agent_stability_guard_db.py` | `737ed6eee043a2e3f67c58b1bd5ae8efde5c31a8` |
| +121 | 0.813 | `memory/tests/test_active_recall_hook.py` | `6776e745521dc92d0a537baeb78f1c852b50f8b2` |
| +120 | 0.976 | `memory/soul_memory_sdk_api.py` | `63c5348908f836337ba111fc95bfaf145ded5394` |
| +110 | 0.971 | `seal-desktop/companion_core/ui/src/views/OpenClawCatalogView.tsx` | `9c45b372b039919750722aa7eea419489e5b2972` |
| +106 | 0.965 | `tools/install-soul.sh` | `a5243ec5b3c1ad2e7b58b66bd04cb66113080cfc` |
| -95 | 0.964 | `memory/consolidation_daemon.py` | `e6cac4c1785458fa251e9898deb30de180d3238c` |
| +87 | 0.694 | `agents/ALICE/v2_shadow/tests/test_planilla_ciega_v1.py` | `18dfb6b3ac6fefae7416104efb1a4a2d1b05c98f` |
| +86 | 0.898 | `messages/whisper_daemon.py` | `85a8ba59950df7678a656d8207817191371ac386` |
| -80 | 0.963 | `memory/seal_bench.py` | `8606a1f46802ad0d5ec39b8a49fcce8cde39d50b` |
| +77 | 0.783 | `messages/capabilities.yaml` | `e1c8eb947ad4801d0aeb589d113398432e043975` |
| +74 | 0.991 | `memory/soul_cognitive_graph_viewer.py` | `ab3f1eca04df082228072b1748e144b2502bcb14` |
| +71 | 0.922 | `memory/migrations/055_soul_sdk_tenant_role_boundary.sql` | `3d6b57f9b1b29093c15669ae54e1287b5142934f` |
| +71 | 0.801 | `messages/seal_context_guard.sh` | `893fd97b1acdce25fe4f215faf97afa68f475627` |
| +69 | 0.881 | `memory/instinct_cron.py` | `8ce31dcad2dd90730af24b0aeef5b13d6b3f2db3` |
| +68 | 0.799 | `memory/soul_boot_hook.sh` | `8b942ce9ec68f2756568efe940a09f5e5b67ac12` |
| +67 | 0.93 | `soul-platform/tests/test_autowire_v1.py` | `d8ee76ff5631d52341b67fadfb959b970e7f34d7` |
| +65 | 0.754 | `messages/whisper_send.py` | `e94b978b8f7bb8299e1c9e976fcb3db56ac90ecd` |
| +64 | 0.807 | `tools/soul_moat_inventory.py` | `9451e66b4e9dc36996cd55ef72c75ec3a60d057c` |
| +63 | 0.851 | `memory/auto_extract_llm.py` | `7a97e86a711d4eeb9d4ccef9059f3d52f2f106c3` |
| +63 | 0.691 | `nexus.sh` | `ad2734e6a4775e0a8577e8d49432f8063fea3960` |
| +62 | 0.771 | `messages/ws_listener.py` | `da43d25f2e18a0e334b02687014ced0819332d0f` |
| +60 | 0.991 | `agents/NEXUS/test_nexus_heartbeat_seat.py` | `6bffb2fc4ae5092d8cc807c88e9f7bd6193234b7` |
| +57 | 0.793 | `agents/ALICE/v2_shadow/armar_planilla_ciega.py` | `db0a8f95f129c3e82a18fc70255282d26f8c51ce` |
| +57 | 0.74 | `seal-desktop/companion_core/tests/test_openhuman_remaining_gaps.py` | `f82e7850b767cc018d24c4bfdb954344228af0a4` |
| +54 | 0.746 | `jarvis_fresh.sh` | `eb9f8d5dcbfe62d26da7ec3d4edeac10533075f6` |
| +54 | 0.793 | `memory/post_compact_hook.py` | `68deb0317cf454d87f9a70ccbae1aeef17a9d7c2` |
| +53 | 0.86 | `docs/schemas/nerves_orchestrator_receipt_v1.schema.json` | `0a70fd07e289d760e2de5a1e0903ef705479e801` |
| +53 | 0.795 | `soul-platform/src/soul_platform/autowire/discovery.py` | `56b344ef953855b350bc0deb77b2bd9ebe1b6460` |
| +51 | 0.933 | `fable/rigor_claim_audit.py` | `c11a7e655bdc9b5044ce20d8d6ebadac36a13048` |
| +51 | 0.918 | `memory/ada_boot_test.py` | `34f293cc9b9c59c189dcf993d71fb2de74a4126a` |
| +51 | 0.926 | `memory/test_mcp_runtime_fail_closed.py` | `a9baa7af9e45a32a3d851315e463be56471286c9` |
| +49 | 0.888 | `memory/minisoul_sync_central.py` | `0cf2286d6cc677f14f66ef7ad621b74258b4f2c0` |
| +49 | 0.939 | `seal-desktop/companion_core/companion_core/byok_vault.py` | `4f9e184ce7b65b87894876726ca43c2a02a848e5` |
| +49 | 0.939 | `seal-desktop/debian/seal-companion/usr/share/seal-companion/companion_core/byok_vault.py` | `4f9e184ce7b65b87894876726ca43c2a02a848e5` |
| -46 | 0.894 | `soul-platform/tests/test_mcp_stdio_v1.py` | `f9291a4606eafb259155564abafdc9b0c234291d` |
| +41 | 0.805 | `alice_fresh.sh` | `0fd30b8de73f41784e13888d00a504bd404e261d` |
| +38 | 0.93 | `seal-desktop/companion_core/ui/src/views/ConnectionsView.tsx` | `e1f01a236edbaa6e222921696e8400ebe07ee88c` |
| +37 | 0.915 | `memory/session_chain.py` | `a181130f2e10fb819c1c55f3b33f98b3f591b9d2` |
| +37 | 0.504 | `messages/send_webchat.py` | `c8c695ee9457641762cda188203f26e7456be562` |
| +36 | 0.916 | `seal/doctor.py` | `9e37ef878984ada5216a2d6a35deeb2f40525527` |
| +36 | 0.862 | `seal_restart.sh` | `913656d7954c557e40c5d8ab8dd457fcaa095688` |
| +35 | 0.94 | `memory/soul_lite_adapter.py` | `bfd858d392a126f30195322c328ef248615f33c8` |
| +34 | 0.914 | `ada_codex.sh` | `8870ab6d8eff415ed1316d2292dee22f06e4dd56` |
| +34 | 1.0 | `agents/NEXUS/gate_arranque_clon.py` | `1e93679645d99c0045d2c9b644a5d252722e48b8` |
| +33 | 0.945 | `memory/test_soul_memory_sdk_keys.py` | `939bf6cc3647e75607c922212fda694950960683` |
| +32 | 0.947 | `memory/daily_brief_writer.py` | `690229d9e8fef7396b938c0dd77945d2dd3bc671` |
| +32 | 0.88 | `memory/db.py` | `58c1de99b5c0318f963fd03796f077f6a5f90d83` |
| +32 | 0.922 | `messages/tests/test_chat_upload_contract.py` | `7c420d89a4e5f6fecd10cf3943ba3fe8b54ca072` |
| +32 | 0.943 | `tools/nexus_nerves_watch.py` | `da125d3c45c646b4aa1be126c6951cb1d2ff5d78` |
| +31 | 0.921 | `messages/seal_durable_cron.py` | `a00875cc2537b7b5b529f5725377ddcfb388301f` |
| +31 | 0.984 | `tools/english_assistant/english_assistant_v4.py` | `46b497c8fcf1fcce7e6f662aa830297268c40cfb` |
| -30 | 0.942 | `memory/cognee_ingest.py` | `6fd7241284500e61e436bf2a5c89feec260861e2` |
| -26 | 0.937 | `memory/soul_maintenance.py` | `da2dc586562c61f190e64fc02957baa9ff572934` |
| +26 | 0.915 | `soul-dashboard/frontend/src/components/sections/NexusReviewQueueSection.tsx` | `bb88968a2aeb20f437bf12e0b14e18263b8f513f` |
| +25 | 0.979 | `memory/sleep_gate_cron.py` | `ca2d0b3681def8f5776646ae2b7435edd8e81499` |
| +24 | 0.853 | `messages/tests/test_nexus_cascade_timeout_v1.py` | `d9fd49462ad7f886d1b9be45f3413f31b459f834` |
| +23 | 0.971 | `memory/context_manager.py` | `46320d4161e383b7c260c8e747c5965d19e0192a` |
| +23 | 0.942 | `memory/test_privacy_enforcement.py` | `cfc43d734eed4197017a8375c0cfed3e2ce4ec38` |
| +23 | 0.931 | `messages/dum_watchdog.py` | `7516e72f8930477eeacc8e21db4c9c10b1169781` |
| +23 | 0.939 | `seal-desktop/companion_core/ui/src/components/AddAccountModal.tsx` | `a33a4518142dd65c9502b92bd9951dade563a343` |
| +22 | 0.962 | `fable/fable_nerves.py` | `c664966a5c69cafa4d025406850ddbf4447e921c` |
| +22 | 1.0 | `memory/nerves_mission_handoff.py` | `c28f76f4373ce394be96b123637a391b930610de` |
| +22 | 0.965 | `seal-desktop/companion_core/companion_core/db.py` | `0059d786afb058f72725511a67e90086d1e7508c` |
| +22 | 0.965 | `seal-desktop/debian/seal-companion/usr/share/seal-companion/companion_core/db.py` | `0059d786afb058f72725511a67e90086d1e7508c` |
| -21 | 0.963 | `memory/session_distill_pipeline.py` | `e314b8d360444689a4cb30f163e75714bbb45a2a` |
| +21 | 0.869 | `scripts/soul_ingestion_healthcheck.py` | `269bf2b847b2f6befc99be6104bd98fb15e1eb6a` |
| +21 | 0.962 | `seal-desktop/companion_core/ui/src/views/SettingsView.tsx` | `d5b61feeecc55475a6e6ba0de5f74dbdce9c351e` |
| +21 | 0.904 | `soul-dashboard/frontend/src/components/sections/ThoughtsSection.tsx` | `5c26175f0be52f63a88b20ac3cb6a97f92c04aa8` |
| +20 | 0.82 | `quality/manifests/fable-seat-detector.json` | `e8e529ad6ddad2144ec7424983e4ed015747ba51` |
| +20 | 0.924 | `soul-dashboard/frontend/src/components/sections/DiarySection.tsx` | `3cb45450e11a055b2ef2b2e81c821c73dc0317e5` |
| +19 | 0.801 | `quality/manifests/nexus-storea-rotation-oracle.json` | `8bb25843d2eef8c2214aa1e982d9eef5cd2a7884` |
| +19 | 0.958 | `soul-platform/src/soul_platform/autowire/manager.py` | `b263b6f1e55a6c1f2501daa7e65aec26c86fffe6` |
| -18 | 0.948 | `memory/soul_consolidate.py` | `d53f9c1045934ab9706d1da0c8678014aa11d643` |
| +18 | 0.762 | `messages/seal_durable_loops.json` | `0c637a0b481db06147ad1b40b1d35b7702757e7f` |
| +18 | 0.976 | `tools/nerves_queue_depth.py` | `641341e59d8581802e4b11c3202469a3a6862dd4` |
| +18 | 0.958 | `tools/test_nerves_queue_depth.py` | `b595b9af81417ca20e9da823c6d1567002e7b337` |
| +17 | 0.962 | `messages/seal_agent_resurrect.py` | `85ab513bbad3a227b352abd52b32bc45fbc81fee` |
| +17 | 0.857 | `seal_common_env.sh` | `341ec3ffbb9af117df4bf273b3b8c745dbef827c` |
| +17 | 0.783 | `memory/post_compact_session_start_hook.py` | `c818ae6cab775d72acc051e96eff3acf8654bc8b` |
| +16 | 0.939 | `fable/fable_recall_hook.py` | `08d0b15377063e73a8abd27b54c241445bd9b885` |
| +16 | 0.674 | `messages/seal_channel_monitor.sh` | `addf9db4acb91d2e669e284daae99b758fed18f2` |
| +16 | 0.684 | `quality/mutation-nexus-seat-detector.json` | `367d6b7b854a99517fffe2a1bf32fcdf9da96e12` |
| +15 | 0.97 | `memory/jarvis_cmd_executor.py` | `917d50a1c8c05a36bb120c53fb43b115bd159e90` |
| +15 | 0.8 | `memory/session_capture_hook.sh` | `95c551129d2a4618fd32180659e9ba7d964107dd` |
| -15 | 0.974 | `messages/seal_resurrect_panel.py` | `1780fb3f1a803dfaf693eb3f34bce6e0312065a1` |
| +15 | 0.949 | `soul-platform/tests/test_installer_machine_v1.py` | `a226e7a53f5b95a7bf7912c6869919859da71c3e` |
| +14 | 0.948 | `memory/sycophancy_eval_suite.py` | `0f9267e7953a415d4513fa2bf0823451617428da` |
| +14 | 0.908 | `messages/ops_monitor.sh` | `ee0439e764729696e6700380b1dd73c6b9b46ece` |
| +14 | 0.939 | `seal/worktree.py` | `8ea9ad5dfbc1f784a1a436a72ec2324cd19c8321` |
| +14 | 0.945 | `soul-dashboard/frontend/src/components/sections/WilliamReviewSection.tsx` | `c64045ed72e2a1ca2ce201c22f5b10543360ebb8` |
| -13 | 0.977 | `memory/test_nivel2.py` | `0208e2908269814dcd5efa8f7a233d1ccb47d45b` |
| -13 | 0.973 | `memory/transcript_streamer.py` | `a7305f90c31265a644e93d67347e92f369420295` |
| +13 | 0.786 | `messages/jarvis_process_heartbeat.sh` | `6cb21f0a4ff00880769cf624123eef14f4c10f22` |
| +13 | 0.822 | `messages/jarvis_research_trigger.sh` | `ac216ad1756f73fbe4dd93a639a0cde9a26f48c4` |
| +13 | 0.975 | `seal-desktop/companion_core/ui/src/App.tsx` | `338beb64a9fb1683acecf8608f7f6135a291952c` |
| +12 | 0.975 | `memory/ada_cli.py` | `b948a2221d1711dbe287d6292aeba267d4640448` |
| -12 | 0.994 | `memory/memory_lifecycle.py` | `0840676539c52c5b6ad3e85e0c7bc4cdf5d37441` |
| +12 | 0.985 | `memory/test_soul_sdk_tenant_role_boundary.py` | `318a34749a0986f77e317366c42709738fbca27e` |
| -12 | 0.87 | `quality/manifests/alice-v2-baseline-tools.json` | `46a416b2cff0f94f6fe163a1038349596f1150b8` |
| +12 | 0.957 | `SEAL_MASTER_DOC/boot.py` | `097d8f4999aa26a507aadda77c9847f8987361fd` |
| +12 | 0.957 | `seal-runtime/core/boot.py` | `097d8f4999aa26a507aadda77c9847f8987361fd` |
| +11 | 0.978 | `agents/JARVIS/tests/test_jarvis_tooling_v1.py` | `e2b24f366072e26c45d98d093ebc32652d8ae0b5` |
| +11 | 0.958 | `memory/hooks/edit_precision.py` | `e02f113cbbcdbec59354d7988d9ad8dcaa5d7910` |
| +11 | 0.964 | `memory/hooks/pre_scope_check.py` | `586a6f47534b54211a8bd6aae0fec0bdbb15d6d7` |
| -11 | 0.966 | `memory/memory_extractor_agent.py` | `aebf04a0e26d96fc14c5ab42cacc265eb8c33137` |
| +11 | 0.991 | `memory/soul_memory_sdk_keys.py` | `577a7058da4c72ade29fcf5576a975da442f90c8` |
| +11 | 0.842 | `messages/ada_dm_poller.py` | `d242e7cb08f0f812ac9a0c676076fd1aaf1e8702` |
| +11 | 0.975 | `seal/credential_pool.py` | `635e9c2af7acdd9ccc08758b9aaf6d65edf277b4` |
| +10 | 0.976 | `agent_monitor.py` | `6d070cbb773a2655ea0f28716ae36456f8c93391` |
| +10 | 0.973 | `fable/injection_robustness_benchmark.py` | `af02f2acdec38fa32cf23e717ef5ab29fb672bed` |
| +10 | 0.981 | `SEAL_MASTER_DOC/skill_loader.py` | `b0c38e3a3fd572900c77b1125adf7ade2ad6b6e4` |
| -10 | 0.986 | `seal/tests/test_doctor.py` | `bf3a0eda2411a1f4ac6fb9e7ce7dfd8f4c8be3cd` |
| +10 | 0.981 | `skills/seal-runtime/skill_loader.py` | `b0c38e3a3fd572900c77b1125adf7ade2ad6b6e4` |
| +9 | 0.868 | `memory/stop_hook.py` | `cb312193976097acf1482ca5d6bf3be1ffa357fe` |
| +9 | 0.948 | `seal_relaunch.sh` | `d2511bd83b0db12ecc80a455f204fc17624be370` |
| +9 | 0.995 | `tools/seal_identity_lifecycle.py` | `4e27dcb636d65ef671ed481c8bf5f44d3cd251ba` |
| +8 | 0.987 | `memory/hooks/post_edit_checkpoint.sh` | `aa9367a2908b8b4f156a1863ecf1d066ce895f8a` |
| +8 | 0.972 | `memory/session_handoff_hook.py` | `e4f997592bad6d4abf73be34083f489d97428159` |
| +8 | 0.989 | `messages/soul_v3_studio_server.py` | `8ed317dcaf3736f0740db5b107e6533d29d22511` |
| -8 | 0.989 | `sandbox-agent/ADA/kernel/cortex.py` | `57b2c03cd0426c1bd22cd62832a4f698ba44ecb4` |
| -8 | 0.979 | `sandbox-agent/ALICE/kernel/cortex.py` | `923a2eccdca899d5a6c2cf6acf1afb3dcb1baaaf` |
| -8 | 0.982 | `sandbox-agent/JARVIS/kernel/cortex.py` | `a47e614cb682038fd37973c61f5c38541384878d` |
| -8 | 0.983 | `sandbox-agent/NEXUS/kernel/cortex.py` | `7131102d843c753d212c806523e1732a2b62fc57` |
| +8 | 0.993 | `SEAL_MASTER_DOC/query_loop.py` | `ef39a1377013e468c0d0e70a9865ce478b2d3b15` |
| +8 | 0.967 | `SEAL_MASTER_DOC/tool_registry.py` | `7aa928fa68cd5fc07dd674d7b8e94099e4d52638` |
| +8 | 0.964 | `soul-dashboard/frontend/src/components/sections/Snapshot.tsx` | `1d44178465037ab7953897d129cce5f4c150d08f` |
| +8 | 0.967 | `soul-dashboard/frontend/src/components/sections/CostSection.tsx` | `9f4160e5e5852a1a575fdb6f3495f99f3b2b1246` |
| +7 | 0.974 | `memory/soul_event_interface.py` | `7977110d312346553cc982382d510b252181d363` |
| +6 | 0.978 | `memory/hooks/pre_edit_checkpoint.sh` | `a9a7e97eedb6d35e5dd36a3b0ce5ca1cc921ca8c` |
| +6 | 0.995 | `SEAL_MASTER_DOC/bridge.py` | `70c896cec21ab161fe1a265dc8b722462f354a9e` |
| +6 | 0.985 | `SEAL_MASTER_DOC/session_manager.py` | `ac755ec42d257b2fc1bd9f2d41bf21226d7ae021` |
| +6 | 0.995 | `seal-runtime/api/bridge.py` | `70c896cec21ab161fe1a265dc8b722462f354a9e` |
| +6 | 0.968 | `soul-platform/tests/test_windows_bundle_v1.py` | `d81c096d36ed714097721ace7fdd4a1521b6c892` |
| +5 | 0.933 | `fable/remember.py` | `92e0d0caacb82bd77c3aa07e285df9468d23a8d0` |
| +5 | 0.991 | `memory/skill_instinct_factory.py` | `c0ea9f8579ab82d62f8cc5391a14ff9b4c8178b4` |
| +5 | 0.96 | `messages/peer_health_check.sh` | `7f810392005169895810dde7da0500a709a27f95` |
| +5 | 0.902 | `messages/routing.yaml` | `58bddaab170c70c0f4961f896a0d4034473226ad` |
| -5 | 0.987 | `sandbox-agent/NEXUS/kernel/executor.py` | `da65c649ff81e188634befa95dda7924141b78d2` |
| +5 | 0.983 | `tools/seal_mtls.py` | `7bd1c59c3a909761401277be2ca877d4f97d5cc3` |
| +4 | 0.989 | `memory/diagnostic_eval_extended.py` | `eb65a22e255b11997ec2b0d55e92686ef8f5e315` |
| -4 | 0.983 | `memory/idle_curiosity.py` | `1e3bc6cf6f1786c30b95bf7d7a2534d17188cd53` |
| +4 | 0.994 | `memory/jarvis_local_agent.py` | `25314c64ec68d20578437e9b607d9003893d2e45` |
| +4 | 0.992 | `sandbox-agent/NEXUS/handlers/nexus_handlers.py` | `4706709c71f0b01a661e4699cdbb522132712c22` |
| +4 | 0.895 | `sandbox-agent/nexus_liveness_check.sh` | `66606df22a6fe72576b2b4e4eb2b290339871785` |
| +4 | 0.985 | `seal/cli.py` | `364eb35dffd233cf24c7204e1ecef53d413f701a` |
| +3 | 0.945 | `ada_speak.py` | `ec980b4421c9868a0cb0f94d77faaf9087f3ce8c` |
| +3 | 0.979 | `fable/test_release_reproducibility.py` | `e823c4d9fb901e86735a499e4d22b8d2ebdbbf1f` |
| +3 | 0.975 | `memory/denial_tracker.py` | `ea68d2fa22dba0e5ca82e74a619a6857f92004a7` |
| +3 | 0.996 | `memory/soul_map_exporter.py` | `7f39594bf1bfdfa35c53ed414dba59817ff72941` |
| +3 | 0.992 | `memory/soul_memory_sdk_controls.py` | `70c8589c809c798e6af01160472ed23e7edcb20d` |
| +3 | 0.931 | `memory/turn_extract_stop_hook.py` | `c393d9e2d8531732d12632076db69185bc186c43` |
| +3 | 0.986 | `messages/agent_bridge.py` | `294a2e99c40868449f9a86fec7519d32840d32c2` |
| -3 | 0.987 | `sandbox-agent/ADA/kernel/ada_kernel_main.py` | `9f1a5f067ec58bdcae95853000e77a0641ea3bb7` |
| -3 | 0.977 | `soul_wake_all.sh` | `db674d1b11e6fa2d5bdf15aabf90aa9c8ffc36cc` |
| +2 | 0.992 | `fable/test_mcp_web_soul_release.py` | `af130b431faebf4ea1ca7cefd33b127bd4f24f60` |
| +2 | 0.992 | `memory/embeddings.py` | `c99f0b3482f281aa686298a3844488d772f02c8e` |
| -2 | 0.994 | `memory/hooks/test_sprint1_hooks.py` | `cb80d7a238b5ce30fdc21869a76093059ed09dfd` |
| -2 | 0.984 | `memory/secret_scanner.py` | `a5992ff9687dccd55ce267154ff2d7de37a7e2c4` |
| -2 | 0.958 | `messages/external_health_monitor.sh` | `e9c4895b958f8cc31737f9d412b5442c770b761c` |
| -2 | 0.911 | `messages/soul_health_check.sh` | `d2fbf4574043fa97dfaaf32d656e6ff39f7c3c7d` |
| +2 | 0.994 | `soul-platform/tools/build_windows_bundle.py` | `c0909051176a35ef4a7130b7ef80f1653a0c64c1` |
| +1 | 0.993 | `ada_voice.py` | `13841337d0c4e7270a4f8d83465478a4634adf01` |
| +1 | 0.997 | `memory/anti_sycophancy.py` | `2b2d17883024aff5ec882dfa6c5d62c7552b9177` |
| -1 | 0.94 | `memory/backfill_trace_links.py` | `3abac97983ee21e4c3319714fa8577dc108c262d` |
| +1 | 0.995 | `memory/boot.py` | `0d5946047f323925b86cd9cd3f4d8aca0c4e40a2` |
| +1 | 0.998 | `memory/causal_graph.py` | `472a94d2eeeeda83501d09c524f5f979681b9353` |
| +1 | 0.997 | `memory/context_compressor.py` | `ad6a8207bf6620796c47c6ed4f7f3eb4bafb539b` |
| +1 | 0.998 | `memory/daily_sleep.py` | `5341013410abc978a1cb6ff657551229c60e7696` |
| -1 | 0.981 | `memory/denial_tracking_hook.py` | `fee4972e7a6fd08b84f57b1d7d1af5f6260f27cf` |
| +1 | 0.998 | `memory/emotional_variance.py` | `bb1792ecab08aaeb854411ea9311bdec52a4fe83` |
| +1 | 0.996 | `memory/encode_subgraphs_batch.py` | `a6f71a0c5faaa852eb372189bf6877ebf9633487` |
| +1 | 0.996 | `memory/identity_probe.py` | `38b18337eacfc7ea87f7c5b5d95edfe3aae861b2` |
| +1 | 0.984 | `memory/jarvis_audit.py` | `5953cb262e51a5d1accb2c2d4fef97439c61e256` |
| +1 | 0.994 | `memory/jarvis_soul.py` | `2b13bf9afd0ef6f42515f773dac09feabd4b1d91` |
| +1 | 0.994 | `memory/kairos_daily_log.py` | `37a1b89df62a72e7f5a8d811a89b7ca2f740d3bb` |
| +1 | 0.997 | `memory/latent_graphmem_build_pairs.py` | `a0ebd22f831fd27cfa1bffaf876f11f25ae84105` |
| +1 | 0.995 | `memory/latent_graphmem/contamination_check.py` | `0362c5b3f02dff3a27cb7debca370ad0de54d34d` |
| +1 | 0.993 | `memory/latent_graphmem/data.py` | `ce4565255b3146fce304a463bd25b0d9766c392b` |
| +1 | 0.997 | `memory/latent_graphmem_gen_queries.py` | `c331794522fb2a1f40cce8440e2cfa85cfad998c` |
| +1 | 0.997 | `memory/migrate_sealcom.py` | `20825bb78182240d1e1340011e8f62e05c65c6b1` |
| +1 | 0.995 | `memory/migration_validator.py` | `3992dbe94ad1ed436439108e108a83456f340fa8` |
| +1 | 0.998 | `memory/ocean_adaptive_schema.py` | `8cd46b22350edb9f151828d031670af9592624d3` |
| +1 | 0.995 | `memory/post_boot_verify.py` | `49ab2330a73e300e95249932d252c282e550c19c` |
| +1 | 0.997 | `memory/post_train_audit.py` | `2c2fcff8f99101ce50ce8043cfdfbc10229b82cb` |
| +1 | 0.996 | `memory/pre_sleep_distill.py` | `84460b1a26e73c0a3e68f575bd228c7b73b4a663` |
| +1 | 0.997 | `memory/seal_exec_control.py` | `a4101060d1b57f8f20280693874e8b4c0e3d75c2` |
| +1 | 0.993 | `memory/seal_schema_check.py` | `b5fd35a19763e8a65700d524bc7722b85842685f` |
| +1 | 0.997 | `memory/session_delta_capture.py` | `837cbad247d5b5082a49bc8805519b30fe2ce99e` |
| +1 | 0.994 | `memory/soul_diagnostic_cron.py` | `e6121ef0f27d6c1d11e705365cd33d30d7bc52e0` |
| +1 | 0.991 | `memory/subagent_start_hook.py` | `81f61036275f62fe7b1607e8025cee422c0aeb42` |
| +1 | 0.997 | `memory/sync_connectome.py` | `78defa6b606327cb5283adb39942436bdd68115d` |
| +1 | 0.996 | `memory/test_drift_monitor.py` | `77f081a82d873e865c3555df4359f74c26bb2db6` |
| +1 | 0.998 | `memory/test_new_tools.py` | `eb2f108543bf60333126c724d313496f7d0d2a44` |
| +1 | 0.997 | `memory/test_token_savings.py` | `7384839b5a20f4d4c0487a1adfb56be9b01e1e03` |
| +1 | 0.997 | `memory/trie_index.py` | `7f60e25fffcff3b48fdbdd6d9112d00bc75a73cd` |
| +1 | 0.998 | `memory/weekly_sleep.py` | `c7720b024b2c6eab445db895bb2eb2779518baa2` |
| -1 | 0.984 | `sandbox-agent/NEXUS/kernel/health_monitor.py` | `4da4e64f078da5918e8744103e321c25420f4723` |
| +1 | 0.972 | `seal-desktop/build_debs.sh` | `af3505fc750732105028abd0fe2e1575898ee977` |
| -1 | 0.938 | `seal-desktop/companion_core/tests/test_sub_agents.py` | `e0046cb20d5d89063f3c8c80f36f022ed97ce119` |
| +1 | 0.94 | `soul-dashboard/frontend/src/main.tsx` | `218d5d9a933ab2c2297d8c52fe027e94c5533c37` |
| +1 | 0.979 | `soul-dashboard/frontend/vite.config.ts` | `b04288c6941015bfdb6a18194c5d06cba3f9d8cb` |
| +1 | 0.994 | `soul-dashboard/frontend/src/components/sections/AwarenessDashboardSection.tsx` | `ae7e5a031cc37482d3115a5f7d743ef424e6e660` |
| +1 | 0.995 | `soul-dashboard/frontend/src/components/sections/EvidenceDashboardSection.tsx` | `cee224a5c1c1a8d972ee74136e413d96e2e1a261` |
| +0 | 0.997 | `launch_jarvis.sh` | `756357d3fb24050eaa504ca07fac3dff83e25739` |
| +0 | 0.994 | `memory/hooks/post_edit_backtranslate.py` | `410d76e0d20fdaf43ef71f8a5a239020b7e06d38` |
| +0 | 0.994 | `memory/hooks/post_edit_diffcheck.py` | `502174d9d010919eee628f1fb3c5a2a9fb809ab5` |
| +0 | 0.999 | `quality/mutation-recall-router-bm25-fallback-20260902.json` | `23c9768a9aeb96ed6f7f9cd1e08fff0e295cfcdc` |
| +0 | 0.985 | `sandbox-agent/ADA/kernel/executor.py` | `b59f7e3daffae38be771dc1ce284ff82bc386c30` |
| +0 | 0.989 | `seal-desktop/src-tauri/tauri.conf.json` | `acf0316c7f17150a50ba38f93411525c783b4b0e` |
| +0 | 0.996 | `seal-desktop/src-tauri/tauri.user.conf.json` | `959d35dd0b97cb25b75d6bd0ed21f8ea0834c701` |
| +0 | 0.995 | `SEAL_MASTER_DOC/db_pool.py` | `d5b7545c85388c3db172ad31212374e85c533a51` |
| +0 | 0.998 | `seal-route.sh` | `fce5eb0f7c5533e52d5cec1f1994993dee18cf56` |
| +0 | 0.995 | `seal-runtime/core/db_pool.py` | `d5b7545c85388c3db172ad31212374e85c533a51` |
| +0 | 1.0 | `soul-dashboard/frontend/src/components/sections/EmotionsSection.tsx` | `17d5ef878895c6da4ebbc7a6601df3ea11b1ce60` |
| +0 | 0.996 | `soul-dashboard/frontend/src/components/sections/MemoriesSection.tsx` | `347a74c98dedb44a7673ba980049803e5f19bc74` |
| +0 | 1.0 | `soul-platform/installer/soul-install.sh` | `ec7bf8025c7f9f1139963ea8393364d15e81bf40` |
| +0 | 0.998 | `soul-platform/src/soul_platform/autowire/types.py` | `d3376982b7467f6280c2661a5e6a0acc67bf6e26` |
| +0 | 0.999 | `soul-platform/src/soul_platform/__init__.py` | `a69a6967a539368eef2999c58e5d24e1e6cb53de` |
| +0 | 0.988 | `soul-platform/tests/test_release_hygiene_v1.py` | `3fffdc2bea8f45fa9089beb50b21d73c5095c6de` |
| +0 | 0.99 | `soul-dashboard/frontend/src/components/sections/AutonomyDashboardSection.tsx` | `00a6094f33bf69ccb5ced5f81473f045703a3648` |
| +0 | 0.974 | `soul-dashboard/frontend/src/components/sections/ContextMeterSection.tsx` | `83ba170b2d9c2ae6367b0369e07a0c12c6fb5564` |
| +0 | 0.983 | `soul-dashboard/frontend/src/components/sections/NervesSection.tsx` | `097f33552f54962e5fb3267af38b35c9e6495cfb` |

## Otros archivos (docs, logs, jsonl) — 31

| Δ líneas | archivo | blob |
|---:|---|---|
| +296 | `soul-platform/installer/Install-Soul.ps1` | `a1aa4ea2427f048971c6f3447a79d248fca435b6` |
| +99 | `memory/logs/edit_precision_audit.jsonl` | `cfdf89bbb9703730ea1e0087ebc818e6908db41e` |
| +88 | `messages/nexus_inbox.jsonl` | `055e2c9a2f72b96ebac5d9e7c8453a0c04a395bf` |
| +63 | `docs/specs/SPEC_SOUL_SOVEREIGN_AGENT_IDENTITY_V1.md` | `3e9d20520dac331965ffd2d97c638d479bc7594a` |
| +58 | `docs/specs/SPEC_SOUL_MODEL_AUTOWIRE_V1_ADA.md` | `76ac220bb338c9857c7c9122d794e92007c96ae0` |
| +27 | `docs/thesis/ssai/RESEARCH_LOG.md` | `e634ddbc6b1794b50acf2983689715e8f6a61103` |
| +23 | `docs/specs/SPEC_SOUL_LEARNING_LAYER_F1_EVENT_INTERFACE.md` | `f31df55899617a2aed3ade3721b17a9865583048` |
| +22 | `AGENTS.md` | `0767bb422f53661723557793d8567fd538b1df06` |
| +21 | `HERMES_ABSORPTION_CHECKLIST.md` | `4116d11ac9a639a82390ecd450b5a3728adf74f0` |
| +17 | `PROYECTO_SEAL_PAPER.md` | `9bb4257b74c27fa4c4e124f8a43afcb4f27046a1` |
| +11 | `docker-compose.seal-memory.yml` | `20f6ce55ea50c982b6be56d782bf8c05b3b3b42a` |
| +10 | `seal-desktop/BUILDING.md` | `4d243ce9a0a4d6d162e25bcdc3950581c8eed525` |
| +4 | `seal-desktop/companion_core/ui/src/index.css` | `823654ff93a67d2599b75434f37df60b5a009568` |
| +4 | `systemd/user/fable-nerves.timer` | `cdb8ae27695546bdd4d059bde88d5f1def89b6e3` |
| +2 | `memory/seal-consolidate.service` | `67203e077c2c2136e57f79cea700513d439d9c53` |
| +2 | `sandbox-agent/CLAUDE.md` | `09c5397900be11782ef5f3e06febdbe18f53cd4d` |
| +2 | `soul-platform/pyproject.toml` | `aefdb4251583c0ae835f4d7c2efe70a15895c67d` |
| +1 | `seal-desktop/companion_core/requirements.txt` | `524172d59dc896dc49d12a9fb1ec08f094b985e5` |
| +1 | `seal-desktop/debian/seal-companion/usr/share/seal-companion/requirements.txt` | `524172d59dc896dc49d12a9fb1ec08f094b985e5` |
| +1 | `seal-desktop/src-tauri/Cargo.lock` | `5a04dec6913e7f9a50ad140de04c330014fa78c0` |
| +1 | `systemd/seal-soul-ingestion.service` | `03c11bd2d2ff2bb1882c90f17be2cb8f04aee0db` |
| +0 | `agents/JARVIS/spec_cascade_mode_20260902.md` | `6dff7811df6fe60da8996b111f7f9ef74521bfce` |
| +0 | `memory/diagnostic/soul_cognitive_graph_view/README.md` | `89b2872a6b8e10bf53d1596e6844299062c1e4b7` |
| +0 | `messages/ada_wakeup.jsonl` | `e07ece449f9442944852a8e527c721a59df66b78` |
| +0 | `messages/jarvis_wakeup.jsonl` | `0f8d9f72f6852ed374a5aaeec62be46fef592757` |
| +0 | `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/index.html` | `b22d9d1924e866ff5ac30ccb49266c28b8b08d38` |
| +0 | `seal-desktop/INSTALL.md` | `8cf8ae6c2c50cd75a2713e0434dab2d7f7c5260a` |
| +0 | `soul-dashboard/frontend/index.html` | `bb7dd43bb7f0d515f242fb9eb7174fff5a4e588f` |
| +0 | `soul-platform/docs/quickstart.md` | `303a99c914d6276219e6202ab4621a2f7aa21e63` |
| +0 | `soul-platform/README.md` | `e458f4467f2c5716cedcd0992df39cc76dfb4545` |
| +0 | `docs/thesis/ssai/MANIFEST.sha256` | `a3fd04821d8a9307784f74eaa3ef724290944dc1` |

## Pisados sin candidato en git (597) — la versión previa sólo existe si un proceso vivo la cargó

- `ada_fresh.sh`
- `ada_launch.sh`
- `ada.sh`
- `agents/ALICE/session_handoff_ALICE_20260506_182149.md`
- `agents/ALICE/session_handoff_ALICE_20260506_182226.md`
- `agents/ALICE/session_handoff_ALICE_20260506_182230.md`
- `agents/ALICE/session_handoff_ALICE_20260506_182235.md`
- `agents/ALICE/session_handoff_ALICE_20260506_182237.md`
- `agents/JARVIS/architecture_map_live.md`
- `agents/JARVIS/seat_lib.sh`
- `agents/JARVIS/session_handoff_JARVIS_20260506_181805.md`
- `agents/JARVIS/session_handoff_JARVIS_20260506_181809.md`
- `agents/JARVIS/session_handoff_JARVIS_20260506_181816.md`
- `agents/JARVIS/session_handoff_JARVIS_20260506_181930.md`
- `agents/JARVIS/session_handoff_JARVIS_20260506_182226.md`
- `agents/JARVIS/spec_heartbeat_zero_token_20260426.md`
- `agents/JARVIS/tests/test_jarvis_seat_lib_v1.py`
- `agents/NEXUS/session_handoff_NEXUS_20260506_181609.md`
- `agents/NEXUS/session_handoff_NEXUS_20260506_181624.md`
- `agents/NEXUS/session_handoff_NEXUS_20260506_181628.md`
- `agents/NEXUS/session_handoff_NEXUS_20260506_181719.md`
- `agents/NEXUS/session_handoff_NEXUS_20260506_181815.md`
- `alice.sh`
- `backups/backup_auto_log.jsonl`
- `fable/test_rigor_claim_audit.py`
- `.githooks/pre-commit`
- `jarvis_briefing.txt`
- `jarvis.sh`
- `memory/conftest.py`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/agents-ada.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/agents-alice.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/agents-dum.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/agents-jarvis.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/agents-nexus.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/artifact-memory-map.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/capability-tooling.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/category-correction.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/category-decision.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/category-emotion.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/category-episodic.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/category-fact.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/category-full-exchange.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/category-insight.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/category-milestone.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/category-open-question.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/category-operational-anchor.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/category-pattern.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/category-preference.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/category-rule.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/category-technical-fact.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/category-trust.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/category-user-request.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/channel-dm-ada-william.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/channel-web-chat.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/contract-silence.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/delivery-completed.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/delivery-milestone.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/evidence-validation.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/intent-channel-rule.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/intent-delivered-artifact.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/intent-identity-presence.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/intent-research-program.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/intent-tooling-permission.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/layer-emotional.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/layer-operational.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/machine-dadito-laptop.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/machines-daditogamer.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/machines-dadito-laptop.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/machines-dgx-spark.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/people-henry.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/people-william.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/person-william.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/program-soul-cortex.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/relation-family.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/state-emotional-presence.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/surface-codex-app-windows.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/surface-terminal.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/systems-ada-codex-bridge.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/systems-codex-app-windows.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/systems-codex.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/system-soul.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/systems-soul-mcp.md`
- `memory/diagnostic/soul_cognitive_graph_view/Facets/systems-webchat.md`
- `memory/diagnostic/soul_cognitive_graph_view/graph.json`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-10032.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-100.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1020.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1022.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1033.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1038.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1048.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-105.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-106.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-107.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-10913.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1104.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1108.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1139.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-113.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1140.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1141.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1142.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1145.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-114.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-115.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-116.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1185.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-119.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1206.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1219.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-122.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1236.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1239.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1249.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-124.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1254.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1259.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1260.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1264.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1265.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1269.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1270.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1271.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1272.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1275.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1276.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1277.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1281.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1282.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1284.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1285.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1287.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1298.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1302.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1305.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1308.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1310.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1311.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1313.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1315.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1316.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-13693.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-138.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-15171.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1569.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-15785.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-158.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-15917.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-15960.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1612.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-162.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-163.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-165.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-16633.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1666.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1683.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-168.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-169.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1729.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1768.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-17741.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1843.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-184.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-185.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-186.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-187.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1880.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-1917.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-193.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-194.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-195.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-196455.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-196456.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-199.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-200.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-202602.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-204091.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-204256.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-204437.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-207025.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-208726.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-209882.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-2121.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-213105.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-213106.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-213107.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-21415.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-217.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-22056.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-22411.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-22781.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-227.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-2280.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-230555.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-230649.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-230650.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-230651.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-230652.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-230859.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-230860.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-230861.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-230930.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-230931.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-230932.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-230934.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-23178.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-233090.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-233139.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-233323.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-233341.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-233365.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-233367.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-233408.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-233502.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-233505.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-233788.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-234073.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-234114.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-234234.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-234804.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-234980.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-235707.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-235944.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-236013.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-236171.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-23650.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-238100.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-238255.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-238277.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-238296.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-238827.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-239775.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-239796.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-239849.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-2400.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-240625.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-240791.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-240868.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-241603.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-241917.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-241921.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-242223.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-242226.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-242228.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-242365.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-242369.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-243113.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-243214.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-243472.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-243534.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-243.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-2452.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-245429.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-245.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-246594.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-246700.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-246761.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-246782.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-246.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-247178.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-247699.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-248035.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-248086.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-248129.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-248403.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-248478.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-248705.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-248706.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-248712.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-248943.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-249020.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-261.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-2634.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-265.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-27596.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-27849.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-282.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-28590.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-28629.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-29379.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-29618.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-29725.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-2.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-30555.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-32083.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-32421.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-32743.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-33297.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-34075.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-34241.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-34351.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-3486.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-3544.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-35.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-362.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-3659.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-37022.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-37027.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-370.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-37331.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-37835.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-3792.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-38152.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-38154.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-38386.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-384.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-39101.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-395.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-40097.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-40816.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-43718.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-43.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-44021.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-44989.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-44.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-46879.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-46.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-47039.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-47070.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-47361.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-47363.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-47522.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-48756.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-4943.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-4965.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-505.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-50.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5203.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5204.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5215.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5216.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5222.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5223.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5225.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5258.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5259.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5262.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5263.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5266.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5272.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5273.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5274.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5277.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5299.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5306.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5309.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5311.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5329.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5342.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5350.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5367.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5368.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5390.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5393.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5394.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5407.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5408.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5415.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5421.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5422.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5423.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5430.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5440.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5444.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5451.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5452.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5456.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5457.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5458.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5460.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5461.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5462.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5463.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5464.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5465.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5496.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5503.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5504.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5528.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5530.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5532.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5534.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5535.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5540.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5547.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5548.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5552.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5554.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5555.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5561.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5562.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5567.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5571.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5578.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5605.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5620.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5621.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5622.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5623.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5626.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-5631.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-571.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-581.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-59.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-606.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-6345.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-6348.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-6596.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-65.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-6650.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-66.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-6785.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-67.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-69.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-71.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-72.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-7496.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-76.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-77.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-7863.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-81.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-83618.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-8679.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-86.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-87.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-886.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-887.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-88.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-928.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-930.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-934.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-944.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-955.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-95.md`
- `memory/diagnostic/soul_cognitive_graph_view/Memories/Memory-99.md`
- `memory/diagnostic/soul_cognitive_graph_view/SOUL Memory Map.md`
- `memory/diagnostic/soul_cognitive_graph_view/viewer_3d.html`
- `memory/diagnostic/soul_cognitive_graph_view/viewer.html`
- `memory/docs/plans/repair_baseline.json`
- `memory/end_session.sh`
- `memory/fix_missing_embeddings.py`
- `memory/logs/post_edit_backtranslate.jsonl`
- `memory/memscenes.py`
- `memory/ocean_protect.py`
- `memory/pre_tool_hook.py`
- `memory/run_consolidation.sh`
- `memory/scripts/reembed_missing.py`
- `memory/scripts/repair_baseline.py`
- `memory/scripts/repair_verify.py`
- `memory/seal-consolidate.timer`
- `memory/seal-ssai-dual-verify.timer`
- `memory/soul_backup_auto.py`
- `memory/soul_reflect.py`
- `memory/task_created_hook.py`
- `memory/test_dual_memory_governance.py`
- `memory/test_soul_memory_sdk_api.py`
- `memory/tests/test_context_v3.py`
- `messages/ada_codex_remote_bridge.py`
- `messages/ada_heartbeat_update.sh`
- `messages/ada_process_heartbeat.sh`
- `messages/.ada_signal`
- `messages/alice_claude_heartbeat.json.tmp`
- `messages/alice_dm_poller.py`
- `messages/.alice_read_count`
- `messages/dum_chat_agent.py`
- `messages/jarvis_dm_poller.py`
- `messages/jarvis_inbox.jsonl`
- `messages/.jarvis_signal`
- `messages/.jarvis_to_ada`
- `messages/nexus_heartbeat_update.sh`
- `messages/post_compact_hook.sh`
- `messages/seal_agent_resurrect.sh`
- `messages/seal_keepalive.sh`
- `messages/seal_monitor_connect.sh`
- `messages/seal_team_meter.py`
- `messages/tests/test_ada_codex_remote_bridge_silence.py`
- `messages/tests/test_soul_council_filter.py`
- `messages/whisper_audit.jsonl`
- `messages/whisper_key_backups/whisper_keys_20260417_165311.json`
- `messages/whisper_key_backups/whisper_keys_20260417_165314.json`
- `messages/whisper_key_backups/whisper_keys_20260428_221759.json`
- `messages/whisper_key_backups/whisper_keys_20260501_000000.json`
- `messages/whisper_key_backups/whisper_keys_20260503_213320.json`
- `messages/whisper_keys.json`
- `messages/.whisper_rate.json`
- `messages/.william_signal`
- `nexus_fresh.sh`
- `ops/systemd/ada-listening-healthcheck.timer`
- `quality/manifests/jarvis-seat-lib-v1.json`
- `quality/manifests/nexus-seat-detector.json`
- `quality/mutation-alice-v2-baseline-tools.json`
- `quality/mutation-jarvis-seat-lib-v1.json`
- `scripts/ada_listening_healthcheck.py`
- `scripts/seal_autonomy_guard.py`
- `scripts/seal_core_guard.py`
- `seal-desktop/build-windows.ps1`
- `seal-desktop/companion_core/companion_core/main.py`
- `seal-desktop/companion_core/companion_core/sub_agents.py`
- `seal-desktop/companion_core/tests/test_memories_chat.py`
- `seal-desktop/companion_core/tests/test_tokenjuice.py`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/companion_core/sub_agents.py`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/AIBackendView-D8IaTq_B.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/AuditLogView-B7uN8TT7.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/avatar-BWE12pYO.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/AvatarView-yyLjby7F.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/BillingView-QvvMPRfB.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/calendar-BdN5Kz8i.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/ChatView-DIxSRa0B.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/chevron-down-BhGKEm7z.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/circle-alert-DDhoj16y.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/circle-check-big-DYZvyrlU.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/clock-BL2AARag.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/ConnectionsView-D-j1G1ZC.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/CronJobsView-ChMIKl6-.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/DreamsView-D5TraFOv.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/eye-DzZpxenl.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/file-text-CX4s1SAT.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/GoalsView-BtohsN7k.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/HumanView-DcRCUiUi.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/image-ByB24Oou.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/index-BPDAv8Fg.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/index-D0wQAkhj.css`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/MemoryTreeView-D6rsn2-I.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/MemoryView-BgK7LJuR.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/NotificationsView-Z5I_1jB2.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/plus-CV8WuQkS.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/PrivacyView-Biv9-Q8U.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/refresh-cw-DpkS10eI.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/RewardsView-DtEiZfo4.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/save-CXQGn2vI.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/ScreenView-DyPFFHqN.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/search-DdjFJl9q.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/send-DbldBM38.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/SettingsView-CG55AfWq.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/SkillsView-DZgha9ht.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/sparkles-Bj8Vl2tb.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/SubAgentsView-D9YJby_D.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/TokenJuiceView-B6E9YAIJ.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/trash-2-pyMyU_8c.js`
- `seal-desktop/debian/seal-companion/usr/share/seal-companion/ui/assets/x-DbGlk7f4.js`
- `seal-desktop/dist/deb/seal-companion_0.3.0_arm64.deb`
- `seal-desktop/dist/deb/seal-team-dashboard_0.1.0_arm64.deb`
- `seal-desktop/launch-seal-app.ps1`
- `seal-desktop/src-tauri/src/lib.rs`
- `seal_identity_env.sh`
- `seal-runtime/dream/seal_dream.py`
- `seal_wake_headless.sh`
- `soul-dashboard/frontend/dist/assets/index-BA9tOCOI.js`
- `soul-dashboard/frontend/dist/assets/index-Bp9lewSh.css`
- `soul-dashboard/frontend/dist/index.html`
- `soul-dashboard/frontend/src/App.tsx`
- `soul-dashboard/frontend/src/components/sections/AgentControlsSection.tsx`
- `soul-dashboard/frontend/src/index.css`
- `soul-framework/docs/architecture.draft.md`
- `soul-framework/docs/quickstart.md`
- `soul-framework/pyproject.toml`
- `soul-framework/README.md`
- `soul-framework/src/soul_framework/backend/base.py`
- `soul-framework/src/soul_framework/backend/__init__.py`
- `soul-framework/src/soul_framework/backend/postgres.py`
- `soul-framework/src/soul_framework/backend/sqlite.py`
- `soul-framework/src/soul_framework/config.py`
- `soul-framework/src/soul_framework/consolidation/sleep_gate.py`
- `soul-framework/src/soul_framework/embedding/sentence_transformer.py`
- `soul-framework/src/soul_framework/embedding/simple.py`
- `soul-framework/src/soul_framework/identity/manager.py`
- `soul-framework/src/soul_framework/identity/ocean.py`
- `soul-framework/src/soul_framework/__init__.py`
- `soul-framework/src/soul_framework/memory/scoring.py`
- `soul-framework/src/soul_framework/memory/store.py`
- `soul-framework/src/soul_framework/procedures/store.py`
- `soul-framework/src/soul_framework/rules/manager.py`
- `soul-framework/src/soul_framework/soul.py`
- `soul-framework/tests/test_backend_sqlite.py`
- `soul-framework/tests/test_extras.py`
- `soul-platform/installer/LEEME-WINDOWS.txt`
- `soul-platform/src/soul_platform/embedding_cutover.py`
- `soul-web/installer/Activate-Soul-Web.ps1`
- `soul-web/pyproject.toml`
- `soul-web/src/soul_web/core_memory.py`
- `soul-web/src/soul_web/__init__.py`
- `soul-web/src/soul_web/ollama.py`
- `soul-web/src/soul_web/server.py`
- `soul-web/src/soul_web/service.py`
- `soul-web/tests/test_activation_script.py`
- `soul-web/tests/test_core_memory_contract.py`
- `soul-web/tests/test_ollama.py`
- `soul-web/tests/test_server.py`
- `systemd/seal-soul-ingestion-health.timer`
- `systemd/user/alice-orion-nerve.service`
- `systemd/user/alice-orion-nerve.timer`
- `systemd/user/seal-ada-nerves-worker.timer`
- `systemd/user/seal-alice-nerves-worker.timer`
- `systemd/user/seal-fable-nerves-worker.timer`
- `systemd/user/seal-nexus-nerves-worker.timer`
- `tests/test_seal_agent_stability_guard_auth.py`
- `tools/.seal_dm/downloads.json`


_Generado por ADA (sesión Claude) 3-sep-2026 ~16:10. Mapa crudo: JSON en el scratchpad de la sesión; script reproducible: buscar objetos sueltos por mtime 13:25:30-13:27:30 y emparejar por fingerprint + difflib._