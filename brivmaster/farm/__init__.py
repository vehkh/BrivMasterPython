"""The gem farm itself - port of the BrivMaster run-side AHK files:

  ctx.py           the shared runtime context (the AHK g_* globals)
  shared_data.py   IC_BrivMaster_SharedData_Class + settings loading
  logger.py        IC_BrivMaster_Logger_Class
  heroes.py        IC_BrivMaster_Heroes.ahk
  level_manager.py IC_BrivMaster_LevelManager.ahk
  casino.py        EllywickCasino / DialogSwatter / DianaCheese (Functions.ahk)
  route_master.py  IC_BrivMaster_RouteMaster.ahk (incl. online stackers, BrivBoost)
  game_master.py   IC_BrivMaster_GameMaster.ahk
  gem_farm.py      IC_BrivMaster_Run.ahk (main loop + pre-flight check)

Method names follow the AHK originals for line-by-line comparability.
"""
