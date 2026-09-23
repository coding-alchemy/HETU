# 合成失败脚本（fixture，不执行）：模拟一次真实执行失败并按失败信封保留
def main():
    raise SystemExit(1)
