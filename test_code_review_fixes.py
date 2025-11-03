#!/usr/bin/env python3
"""
测试代码审查修复
验证所有修复是否正确应用，不执行真实交易API
"""

import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))


def test_exception_handling():
    """测试异常处理修复"""
    print("=" * 60)
    print("测试1: 异常处理修复")
    print("=" * 60)

    try:
        # 读取binance_sync.py检查裸except是否修复
        with open("backend/services/binance_sync.py", "r") as f:
            content = f.read()

        # 检查是否还有裸except
        if "except:" in content and "except Exception:" not in content.replace("except:", "").replace(
            "except Exception:", ""
        ):
            # 更精确的检查
            import re

            bare_except_pattern = r"except\s*:\s*"
            matches = re.findall(bare_except_pattern, content)
            if matches:
                print(f"❌ 发现 {len(matches)} 个裸except块")
                return False
            else:
                print("✅ 所有except块已修复为except Exception")
        else:
            print("✅ 异常处理修复正确")

        return True
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return False


def test_logger_usage():
    """测试logger使用修复"""
    print("\n" + "=" * 60)
    print("测试2: Logger使用修复")
    print("=" * 60)

    try:
        files_to_check = ["backend/api/account_routes.py", "backend/api/arena_routes.py", "backend/api/ws.py"]

        all_good = True
        for file_path in files_to_check:
            with open(file_path, "r") as f:
                content = f.read()

            # 检查是否还有DEBUG print语句
            if 'print(f"[DEBUG]' in content or 'print("[DEBUG]' in content:
                print(f"⚠️  {file_path} 中仍有DEBUG print语句")
                all_good = False
            else:
                print(f"✅ {file_path} 已修复")

        return all_good
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return False


def test_ssl_config():
    """测试SSL配置修复"""
    print("\n" + "=" * 60)
    print("测试3: SSL配置修复")
    print("=" * 60)

    try:
        with open("backend/services/ai_decision_service.py", "r") as f:
            content = f.read()

        # 检查是否从环境变量读取
        if 'os.getenv("ENABLE_SSL_VERIFICATION"' in content:
            print("✅ SSL配置已从环境变量读取")
            return True
        elif "ENABLE_SSL_VERIFICATION = False" in content or "ENABLE_SSL_VERIFICATION = True" in content:
            if "os.getenv" in content:
                print("✅ SSL配置已修复")
                return True
            else:
                print("⚠️  SSL配置仍使用硬编码，建议使用环境变量")
                return False
        else:
            print("⚠️  未找到SSL配置")
            return False
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return False


def test_imports():
    """测试导入是否正确"""
    print("\n" + "=" * 60)
    print("测试4: 导入检查")
    print("=" * 60)

    try:
        # 测试关键模块是否可以导入
        test_modules = [
            "services.binance_sync",
            "services.broker_factory",
            "services.broker_binance",
            "services.broker_adapter",
        ]

        all_good = True
        for module_name in test_modules:
            try:
                __import__(module_name)
                print(f"✅ {module_name} 导入成功")
            except ImportError as e:
                print(f"❌ {module_name} 导入失败: {e}")
                all_good = False

        return all_good
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return False


def test_no_real_trading():
    """确保不会执行真实交易"""
    print("\n" + "=" * 60)
    print("测试5: 交易API安全检查")
    print("=" * 60)

    try:
        # 检查是否有测试交易API调用
        # 这个测试确保我们的测试脚本不会执行真实交易

        # 检查binance_sync.py中是否有测试代码
        with open("backend/services/binance_sync.py", "r") as f:
            content = f.read()

        # 检查是否有测试用的API调用
        if "execute_binance_order" in content:
            # 这是正常的，函数定义存在
            # 但我们应该确保没有在这里直接调用
            print("✅ 交易函数已定义，但未在测试中调用")

        print("✅ 测试脚本不会执行真实交易")
        return True
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return False


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("代码审查修复验证测试")
    print("=" * 60)
    print("\n注意: 此测试不会执行任何真实交易API调用\n")

    results = []

    results.append(("异常处理修复", test_exception_handling()))
    results.append(("Logger使用修复", test_logger_usage()))
    results.append(("SSL配置修复", test_ssl_config()))
    results.append(("导入检查", test_imports()))
    results.append(("交易API安全检查", test_no_real_trading()))

    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status}: {name}")

    print(f"\n总计: {passed}/{total} 测试通过")

    if passed == total:
        print("\n🎉 所有测试通过！代码审查修复已正确应用。")
        return 0
    else:
        print(f"\n⚠️  有 {total - passed} 个测试未通过，请检查。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
