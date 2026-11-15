#!/usr/bin/python3 -I
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "lib"))
from ubp.ci import main

sys.exit(main())
