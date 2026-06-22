#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智学网阅卷CLI工具 v3.0
流程：启动浏览器 → 用户手动操作 → 确认 → 开始批阅
"""
import sys
import os
import argparse
import time
from pathlib import Path

# Windows 终端颜色支持
if sys.platform == 'win32':
    os.system('')

sys.path.insert(0, str(Path(__file__).parent / 'src'))

from src.config import config
from src.browser import BrowserManager
from src.login import LoginManager
from src.grader import Grader, StudentAnswer
from src.ai_grader import ai_grader, AIGradingResult


class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    END = '\033[0m'

    @classmethod
    def disable(cls):
        for attr in ('HEADER', 'BLUE', 'CYAN', 'GREEN', 'YELLOW', 'RED', 'BOLD', 'DIM', 'END'):
            setattr(cls, attr, '')


if not sys.stdout.isatty():
    Colors.disable()


def print_banner():
    print(f"""
{Colors.CYAN}{Colors.BOLD}+======================================================+
|         智学网阅卷 CLI 助手 v3.0                      |
+======================================================+{Colors.END}
""")


def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def wait_for_user_ready(login_mgr: LoginManager) -> bool:
    """等待用户手动操作完成"""
    print(f"""
{Colors.BOLD}{Colors.YELLOW}==================================================={Colors.END}
{Colors.BOLD}  请在浏览器中完成以下操作：{Colors.END}

  1. 登录智学网
  2. 进入「阅卷中心」
  3. 选择要批改的考试/任务
  4. 选择要批改的题目
  5. 确保能看到学生答题图片

{Colors.YELLOW}==================================================={Colors.END}
""")
    input(f"{Colors.CYAN}准备好后，按回车键继续...{Colors.END}")

    return True


def verify_page(grader: Grader) -> bool:
    """验证页面状态，确认能找到答题图片"""
    print(f"\n{Colors.YELLOW}正在分析页面...{Colors.END}")

    # 先打印页面信息
    grader.debug_page()

    # 尝试获取答案
    answer = grader.get_current_answer()

    if answer and answer.answer_image_data:
        size_kb = len(answer.answer_image_data) / 1024
        print(f"{Colors.GREEN}[OK] 找到答题图片！({size_kb:.1f} KB){Colors.END}")
        if answer.student_name:
            print(f"{Colors.GREEN}  学生: {answer.student_name}{Colors.END}")

        # 保存图片预览
        preview_path = grader.save_current_answer_image("preview_current.jpg")
        if preview_path:
            print(f"{Colors.GREEN}  预览图已保存: {preview_path}{Colors.END}")

        return True
    else:
        print(f"{Colors.RED}[X] 未找到答题图片{Colors.END}")
        print(f"\n{Colors.YELLOW}可能的原因：{Colors.END}")
        print(f"  1. 页面还没加载完，请稍等后重试")
        print(f"  2. 没有进入正确的阅卷页面")
        print(f"  3. 答题区域需要滚动才能看到")
        print(f"\n{Colors.DIM}上方已打印页面结构，请检查图片列表{Colors.END}")

        return False


def setup_question_info(grader: Grader):
    """上传题干图片，AI 自动生成答案、解析、评分细则"""
    print(f"\n{Colors.BOLD}{Colors.CYAN}========== 题目设置 =========={Colors.END}")
    print(f"上传题干图片，AI 会自动生成标准答案、解析和评分细则")
    print(f"{Colors.DIM}直接回车跳过（AI 阅卷将自行判断）{Colors.END}\n")

    img_path = input(f"{Colors.BOLD}题干图片路径 (或回车跳过): {Colors.END}").strip().strip('"')

    if not img_path:
        print(f"{Colors.DIM}已跳过{Colors.END}")
        return

    # 读取图片
    from pathlib import Path
    path = Path(img_path)
    if not path.exists():
        print(f"{Colors.RED}文件不存在: {img_path}{Colors.END}")
        return

    try:
        with open(path, 'rb') as f:
            img_data = f.read()
    except Exception as e:
        print(f"{Colors.RED}读取失败: {e}{Colors.END}")
        return

    max_s = grader.max_score
    print(f"\n{Colors.YELLOW}[1/3] 正在发送题干图片到 AI...{Colors.END}")
    print(f"{Colors.DIM}      模型: {config.AI_MODEL}{Colors.END}")

    try:
        from src.ai_grader import ai_grader
        print(f"{Colors.YELLOW}[2/3] AI 分析题目中，请稍候...{Colors.END}")
        analysis = ai_grader.analyze_question(img_data, max_score=max_s)
        print(f"{Colors.YELLOW}[3/3] 收到回复，解析中...{Colors.END}")

        print(f"\n{Colors.BOLD}{'='*50}{Colors.END}")
        print(f"{Colors.BOLD}标准答案:{Colors.END}")
        print(f"{Colors.CYAN}{analysis.model_answer}{Colors.END}")
        print(f"\n{Colors.BOLD}解析:{Colors.END}")
        print(f"{Colors.CYAN}{analysis.explanation}{Colors.END}")
        print(f"\n{Colors.BOLD}评分细则:{Colors.END}")
        print(f"{Colors.YELLOW}{analysis.scoring_rubric}{Colors.END}")
        print(f"{Colors.BOLD}{'='*50}{Colors.END}")

        print(f"\n{Colors.GREEN}[OK] 题目分析完成，批阅时 AI 将自动批改每份答卷{Colors.END}")
        print(f"{Colors.DIM}按空格可暂停自动批阅，手动输入分数{Colors.END}")

    except Exception as e:
        print(f"{Colors.RED}AI 分析失败: {e}{Colors.END}")
        print(f"{Colors.DIM}批阅时 AI 将自行判断{Colors.END}")

    print()


def _auto_ai_grade(grader: Grader, answer):
    """自动 AI 批阅一份答卷"""
    from src.ai_grader import ai_grader
    max_s = grader.max_score

    print(f"{Colors.YELLOW}AI 批改中...{Colors.END}", end="", flush=True)
    try:
        ai_result = ai_grader.grade(
            image_data=answer.answer_image_data,
            max_score=max_s,
        )

        if ai_result.confidence != 'error':
            if grader.submit_score(ai_result.score):
                print(f"\r{Colors.GREEN}[OK] {ai_result.score}/{max_s}  "
                      f"{ai_result.reasoning[:40]}{Colors.END}")
            else:
                print(f"\r{Colors.RED}[X] 提交失败，查看 data/debug.log{Colors.END}")
        else:
            print(f"\r{Colors.RED}[X] AI 失败: {ai_result.reasoning[:50]}{Colors.END}")

    except Exception as e:
        print(f"\r{Colors.RED}[X] 错误: {e}{Colors.END}")


def grading_loop(grader: Grader):
    """阅卷主循环"""
    from src.ai_grader import ai_grader
    auto_ai = ai_grader.question_analysis is not None

    if auto_ai:
        print(f"\n{Colors.GREEN}{Colors.BOLD}========== AI 自动批阅模式 =========={Colors.END}")
        print(f"{Colors.DIM}已上传题干，AI 将自动批改每份答卷{Colors.END}")
        print(f"{Colors.DIM}按 q 退出  |  按 h 帮助  |  按空格暂停自动批阅{Colors.END}")
    else:
        print(f"\n{Colors.GREEN}{Colors.BOLD}========== 手动批阅模式 =========={Colors.END}")
        print(f"{Colors.DIM}输入分数后按回车提交  |  按 a 调 AI  |  按 h 帮助{Colors.END}")

    answer = grader.get_current_answer()
    score_input = ""
    paused = False  # 暂停自动批阅

    # 自动 AI 批阅第一张
    if auto_ai and answer and answer.answer_image_data and not paused:
        _auto_ai_grade(grader, answer)

    while True:
        try:
            clear_screen()
            print_banner()

            # 进度
            p = grader.progress
            bar = grader.get_progress_bar(40)
            print(f"\n{Colors.CYAN}进度: {bar}  已批: {Colors.GREEN}{p.graded}{Colors.END}")

            # 学生信息
            if answer:
                if answer.student_name:
                    print(f"学生: {Colors.BOLD}{answer.student_name}{Colors.END}")
                if answer.is_graded:
                    print(f"当前分数: {Colors.YELLOW}{answer.current_score}{Colors.END}")

            # 图片状态
            if answer and answer.answer_image_data:
                print(f"{Colors.GREEN}[OK] 答案图片已加载{Colors.END}")
            else:
                print(f"{Colors.YELLOW}[!] 未加载到图片{Colors.END}")

            # AI 分析的评分细则
            from src.ai_grader import ai_grader
            if ai_grader.question_analysis and ai_grader.question_analysis.scoring_rubric:
                rubric_preview = ai_grader.question_analysis.scoring_rubric[:80]
                print(f"{Colors.DIM}评分细则: {rubric_preview}...{Colors.END}")

            # 分数输入
            max_s = grader.max_score
            print(f"\n{Colors.BOLD}满分: {max_s} 分{Colors.END}")

            if auto_ai:
                status = f"{Colors.YELLOW}[已暂停]{Colors.END}" if paused else f"{Colors.GREEN}[自动批阅中]{Colors.END}"
                print(f"{status} 空格暂停/继续  |  回车手动打分  |  q 退出")

                if not paused:
                    # 自动批阅：执行后短暂等待再下一张
                    _auto_ai_grade(grader, answer)
                    answer = grader.get_current_answer()
                    time.sleep(0.5)
                    continue
                # 暂停状态：继续读取按键
            else:
                if score_input:
                    print(f"{Colors.CYAN}输入: {Colors.BOLD}{score_input}{Colors.END}")
                else:
                    print(f"{Colors.CYAN}输入分数 (或按 a 调AI): {Colors.END}", end="")

            # 读取按键
            try:
                import msvcrt
                ch = msvcrt.getwch()
            except ImportError:
                import tty, termios
                fd = sys.stdin.fileno()
                old = termios.tcgetattr(fd)
                try:
                    tty.setraw(fd)
                    ch = sys.stdin.read(1)
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)

            # 处理按键
            if ch in ('q', 'Q', '\x1b'):
                confirm = input(f"\n{Colors.YELLOW}确定退出？(y/n): {Colors.END}").strip().lower()
                if confirm in ('y', 'yes'):
                    break
                continue

            elif ch in ('h', 'H', '?'):
                print(f"""
{Colors.BOLD}快捷键:{Colors.END}
  0-9       输入分数        .         小数点
  Enter     提交并下一份    Backspace 删除输入
  n         跳过            p         上一份
  a         AI自动阅卷      r         旋转图片
  s         保存图片        d         调试(页面分析)
  q         退出
""")
                input(f"{Colors.DIM}按回车继续...{Colors.END}")
                continue

            elif ch in ('d', 'D'):
                grader.debug_page()
                input(f"{Colors.DIM}按回车继续...{Colors.END}")
                continue

            elif ch == ' ' and auto_ai:
                paused = not paused
                if paused:
                    print(f"{Colors.YELLOW}[已暂停] 自动批阅已暂停，可手动输入分数{Colors.END}")
                else:
                    print(f"{Colors.GREEN}[继续] 自动批阅已恢复{Colors.END}")
                time.sleep(0.5)
                continue

            elif ch in ('a', 'A') and not auto_ai:
                # AI 自动阅卷
                if not answer or not answer.answer_image_data:
                    print(f"{Colors.RED}没有答题图片，无法AI阅卷{Colors.END}")
                    time.sleep(1)
                    continue

                print(f"\n{Colors.YELLOW}[1/3] 正在发送图片到 AI ({config.AI_MODEL})...{Colors.END}")
                print(f"{Colors.DIM}      API: {config.AI_BASE_URL}{Colors.END}")
                try:
                    max_s_val = grader.max_score
                    print(f"{Colors.YELLOW}[2/3] AI 分析中，请稍候...{Colors.END}")
                    ai_result = ai_grader.grade(
                        image_data=answer.answer_image_data,
                        max_score=max_s_val,
                        question_info=grader.question_info or "",
                        scoring_standard=grader.scoring_standard or "",
                    )
                    print(f"{Colors.YELLOW}[3/3] 收到回复，解析中...{Colors.END}")

                    # 显示结果
                    if ai_result.confidence == 'error':
                        print(f"\n{Colors.RED}[X] AI 阅卷失败{Colors.END}")
                        raw = ai_result.raw_response or "(空)"
                        print(f"{Colors.RED}{raw[:500]}{Colors.END}")
                        time.sleep(2)
                    else:
                        # 直接提交
                        print(f"\n{Colors.GREEN}[OK] AI 给分: {ai_result.score} / {max_s_val}{Colors.END}")
                        if ai_result.reasoning:
                            print(f"{Colors.DIM}理由: {ai_result.reasoning[:100]}{Colors.END}")

                        if grader.submit_score(ai_result.score):
                            print(f"{Colors.GREEN}[OK] 已提交 {ai_result.score} 分{Colors.END}")
                            score_input = ""
                            answer = grader.get_current_answer()
                        else:
                            print(f"{Colors.RED}[X] 提交失败{Colors.END}")
                            score_input = str(ai_result.score)
                            time.sleep(1)

                except Exception as e:
                    print(f"{Colors.RED}AI 阅卷失败: {e}{Colors.END}")
                    time.sleep(2)
                continue

            elif ch in ('\r', '\n', ' '):
                if score_input:
                    try:
                        score = float(score_input)
                        if 0 <= score <= max_s:
                            if grader.submit_score(score):
                                print(f"{Colors.GREEN}[OK] 提交 {score} 分{Colors.END}")
                                score_input = ""
                                answer = grader.get_current_answer()
                            else:
                                print(f"{Colors.RED}[X] 提交失败，按 d 查看页面结构{Colors.END}")
                                time.sleep(1)
                        else:
                            print(f"{Colors.RED}超出范围 [0, {max_s}]{Colors.END}")
                            time.sleep(0.5)
                            score_input = ""
                    except ValueError:
                        print(f"{Colors.RED}无效分数{Colors.END}")
                        score_input = ""
                continue

            elif ch in ('n', 'N'):
                if grader.go_next():
                    score_input = ""
                    answer = grader.get_current_answer()
                else:
                    print(f"{Colors.RED}找不到「下一份」按钮，按 d 调试{Colors.END}")
                    time.sleep(1)
                continue

            elif ch in ('p', 'P'):
                if grader.go_previous():
                    score_input = ""
                    answer = grader.get_current_answer()
                continue

            elif ch in ('r', 'R'):
                if answer and answer.answer_image_data:
                    grader.rotate_image(90)
                    print(f"{Colors.GREEN}[OK] 已旋转{Colors.END}")
                    time.sleep(0.3)
                continue

            elif ch in ('s', 'S'):
                path = grader.save_current_answer_image()
                if path:
                    print(f"{Colors.GREEN}[OK] 已保存: {path}{Colors.END}")
                time.sleep(0.5)
                continue

            elif ch.isdigit() or ch == '.':
                if ch == '.' and '.' in score_input:
                    continue
                if ch == '.' and not score_input:
                    score_input = "0."
                else:
                    new_s = score_input + ch
                    try:
                        if float(new_s) <= max_s:
                            score_input = new_s
                    except ValueError:
                        pass
                continue

            elif ch in ('\x7f', '\b'):
                score_input = score_input[:-1]
                continue

            time.sleep(0.01)

        except KeyboardInterrupt:
            confirm = input(f"\n{Colors.YELLOW}确定退出？(y/n): {Colors.END}").strip().lower()
            if confirm in ('y', 'yes'):
                break
        except Exception as e:
            print(f"\n{Colors.RED}错误: {e}{Colors.END}")
            time.sleep(2)
            try:
                answer = grader.get_current_answer()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description='智学网阅卷CLI助手 v3.0')
    parser.add_argument('--headless', action='store_true', help='无头模式（不推荐，登录需要可见浏览器）')
    parser.add_argument('--max-score', type=float, default=13, help='满分（默认13）')
    parser.add_argument('--slow-mo', type=int, default=100, help='操作延迟ms')
    parser.add_argument('--no-color', action='store_true', help='禁用颜色')
    args = parser.parse_args()

    if args.headless:
        config.HEADLESS = args.headless
    if args.slow_mo:
        config.SLOW_MO = args.slow_mo
    if args.no_color:
        Colors.disable()

    print_banner()

    print(f"{Colors.YELLOW}正在启动浏览器...{Colors.END}")

    browser = BrowserManager()
    login_mgr = LoginManager(browser)
    grader = Grader(browser)
    grader.max_score = args.max_score

    try:
        browser.start()
        print(f"{Colors.GREEN}[OK] 浏览器已启动{Colors.END}")

        # 打开智学网
        login_mgr.open_login_page()

        # 等待用户手动操作
        wait_for_user_ready(login_mgr)

        # 验证页面
        max_attempts = 3
        for attempt in range(max_attempts):
            if verify_page(grader):
                break
            if attempt < max_attempts - 1:
                choice = input(f"\n{Colors.YELLOW}重试(r) / 强制继续(c) / 退出(q): {Colors.END}").strip().lower()
                if choice == 'q':
                    return
                elif choice == 'c':
                    print(f"{Colors.YELLOW}强制进入批阅模式{Colors.END}")
                    break
                # r = 重试
                input(f"{Colors.CYAN}请确认页面已准备好，按回车...{Colors.END}")
            else:
                print(f"{Colors.YELLOW}多次未找到图片，进入批阅模式后可按 d 调试{Colors.END}")

        # 从页面读取满分
        real_max = grader.read_max_score()
        print(f"{Colors.GREEN}[OK] 从页面读取满分: {real_max} 分{Colors.END}")

        # 设置题目信息
        setup_question_info(grader)

        # 开始批阅
        grading_loop(grader)

    except Exception as e:
        print(f"\n{Colors.RED}程序出错: {e}{Colors.END}")
        import traceback
        traceback.print_exc()
    finally:
        print(f"\n{Colors.YELLOW}正在关闭浏览器...{Colors.END}")
        try:
            browser.stop()
            print(f"{Colors.GREEN}[OK] 已关闭{Colors.END}")
        except Exception:
            pass

    print(f"\n{Colors.GREEN}再见！{Colors.END}")


if __name__ == '__main__':
    main()
