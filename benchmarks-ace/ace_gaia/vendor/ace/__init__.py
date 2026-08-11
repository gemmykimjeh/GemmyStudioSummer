"""ACE core roles and prompts, copied from the reference repository.

The reference package's ``__init__`` re-exports the ``ACE`` orchestrator from
``ace.py``. That file is deliberately not vendored: its ``run`` / ``_offline_train``
/ ``_online_train_and_test`` loops are replaced by the OpenHands ``Evaluation``
loop. The per-sample logic from ``_train_single_sample`` is ported in
``ace_gaia/runner.py``.
"""
