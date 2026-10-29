"""Generic simulation protocol; DUT stimulus and expectations remain model supplied."""
import re


def normalize_stimulus(stimulus):
    # A common model response includes the enclosing initial begin/end despite
    # the fragment API. Keep its entire block and all checks; remove only initial.
    if isinstance(stimulus, str):
        return re.sub(r'\A\s*initial(?=\s+begin\b)', '', stimulus, count=1)
    return stimulus


def build_testbench(declarations, stimulus, checks):
    if not isinstance(declarations, str) or not declarations.strip():
        raise ValueError('testbench_declarations must contain DUT signals/instance and optional clock/tasks')
    if not isinstance(stimulus, str) or not stimulus.strip():
        raise ValueError('testbench_stimulus must contain stimulus and check(condition, id) calls')
    # 片段只能贡献刺激和比较；结束仿真、覆盖和通过标记由框架统一管理。
    code = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/',
                  lambda match: match[0] if match[0].startswith('"') else ' ',
                  declarations+'\n'+stimulus, flags=re.S)
    if re.search(r'\$(?:finish|stop)\b|RTL_SELFTEST_PASS|RTL_CHECK:', code):
        raise ValueError('Fragment completion and coverage are managed by the harness: omit $finish/$stop and PASS/RTL_CHECK markers; use check(condition, registered_id) for each required group')
    stimulus = normalize_stimulus(stimulus)
    ids = [item['id'] for item in checks]
    cases = '\n'.join(f'      "{name}": rtl_covered[{i}] = 1;' for i, name in enumerate(ids))
    markers = '\n'.join(f'    $display("RTL_CHECK:{name}");' for name in ids)
    return f'''`timescale 1ns/1ps
module tb;
  string rtl_token;
  integer rtl_checks = 0;
  reg [{len(ids)-1}:0] rtl_covered = 0;
  task automatic check(input bit passed, input string id);
    rtl_checks = rtl_checks + 1;
    if (!passed) begin
      $display("RTL_SELFTEST_FAIL check=%s time=%0t", id, $time);
      $fatal(1, "comparison failed");
    end
    case (id)
{cases}
      default: begin end // Extra comparison labels do not satisfy required coverage groups.
    endcase
  endtask
  task automatic check_equal(input logic [1023:0] actual, input logic [1023:0] expected,
                             input string id, input string inputs = "");
    if (actual !== expected)
      $display("RTL_SELFTEST_FAIL check=%s time=%0t expected=%0h actual=%0h inputs=%s", id, $time, expected, actual, inputs);
    check(actual === expected, id);
  endtask
{declarations}
  initial begin
    if (!$value$plusargs("RTL_TOKEN_%s", rtl_token)) $fatal(1,"missing token");
    begin : rtl_stimulus
{stimulus}
    end
    if (rtl_checks == 0 || !(&rtl_covered)) $fatal(1,"missing actual comparisons or check coverage");
{markers}
    $display("RTL_SELFTEST_PASS token=%s checks=%0d", rtl_token, rtl_checks);
    $finish;
  end
  initial begin #90000; $fatal(1,"self-test watchdog"); end
endmodule
'''
