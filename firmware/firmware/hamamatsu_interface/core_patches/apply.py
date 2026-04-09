#!/usr/bin/env python

import os
import shutil

Import("env")
FRAMEWORK_DIR = env.PioPlatform().get_package_dir("framework-arduinoteensy")
CORE_PATCHES_DIR = "core_patches"
TARGET_DIR = os.path.join(FRAMEWORK_DIR, "cores", "teensy4")

print("Patching arduino core libs...")

for file_name in os.listdir(CORE_PATCHES_DIR):
  if file_name == "apply.py":
    continue
  src = os.path.join(CORE_PATCHES_DIR, file_name)
  if not os.path.isfile(src):
    continue
  dst = os.path.join(TARGET_DIR, file_name)
  shutil.copy2(src, dst)
  print(file_name)

