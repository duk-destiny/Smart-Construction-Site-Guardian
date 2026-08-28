# 使 scripts/ 成为常规包：防止用户 site-packages 中同名 scripts 包
# （常规包恒优先于 namespace 目录）遮蔽本目录，致 tests 无法 import scripts.*
