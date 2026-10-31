#!/bin/sh
set -eu
ip -o link show
tc qdisc show
