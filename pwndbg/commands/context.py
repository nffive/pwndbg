#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import argparse
import ast
import codecs
import ctypes
import sys
import os
from io import open

import gdb

import pwndbg.arguments
import pwndbg.chain
import pwndbg.color
import pwndbg.color.backtrace as B
import pwndbg.color.context as C
import pwndbg.color.memory as M
import pwndbg.color.syntax_highlight as H
import pwndbg.commands
import pwndbg.commands.nearpc
import pwndbg.commands.telescope
import pwndbg.config
import pwndbg.disasm
import pwndbg.events
import pwndbg.ida
import pwndbg.regs
import pwndbg.symbol
import pwndbg.ui
import pwndbg.vmmap
from pwndbg.color import message
from pwndbg.color import theme


def clear_screen(out=sys.stdout):
    """
    Clear the screen by moving the cursor to top-left corner and
    clear the content
    """
    out.write('\x1b[H\x1b[J')

config_clear_screen = pwndbg.config.Parameter('context-clear-screen', False, 'whether to clear the screen before printing the context')
config_output = pwndbg.config.Parameter('context-output', 'stdout', 'where pwndbg should output ("stdout" or file/tty).')
config_output_regs = pwndbg.config.Parameter('context-output-regs', 'nosplit', 'where register-context should output ("nosplit" or file/tty).')
config_output_disasm = pwndbg.config.Parameter('context-output-disasm', 'nosplit', 'where disasm-context should output ("nosplit" or file/tty).')
config_output_args = pwndbg.config.Parameter('context-output-args', 'nosplit', 'where args-context should output ("nosplit" or file/tty).')
config_output_code = pwndbg.config.Parameter('context-output-code', 'nosplit', 'where code-context should output ("nosplit" or file/tty).')
config_output_stack = pwndbg.config.Parameter('context-output-stack', 'nosplit', 'where stack-context should output ("nosplit" or file/tty).')
config_stack_reverse = pwndbg.config.Parameter('context-stack-reverse', False, 'stack-context to reverse output')
config_output_backtrace = pwndbg.config.Parameter('context-output-backtrace', 'nosplit', 'where backtrace-context should output ("nosplit" or file/tty).')
config_context_sections = pwndbg.config.Parameter('context-sections',
                                                  'regs disasm code stack backtrace',
                                                  'which context sections are displayed (controls order)')

@pwndbg.config.Trigger([config_context_sections])
def validate_context_sections():
    valid_values = [context.__name__.replace('context_', '') for context in context_sections.values()]

    # If someone tries to set an empty string, we let to do that informing about possible values
    # (so that it is possible to have no context at all)
    if not config_context_sections.value or config_context_sections.value.lower() in ('none', 'empty'):
        config_context_sections.value = ''
        print(message.warn("Sections set to be empty. FYI valid values are: %s" % ', '.join(valid_values)))
        return

    for section in config_context_sections.split():
        if section not in valid_values:
            print(message.warn("Invalid section: %s, valid values: %s" % (section, ', '.join(valid_values))))
            print(message.warn("(setting none of them like '' will make sections not appear)"))
            config_context_sections.revert_default()
            return

class StdOutput(object):
    """A context manager wrapper to give stdout"""
    def __enter__(*args,**kwargs):
        return sys.stdout
    def __exit__(*args, **kwargs):
        pass

def show_context(config_output_tty, content):
    with output(config_output_tty) as out:
        if config_clear_screen:
            clear_screen(out)

        for line in content:
            out.write(line + '\n')
        out.flush()


def output(config_output_tty):
    """Creates a context manager corresponding to configured context ouput"""
    if not config_output_tty or config_output_tty == "stdout":
        return StdOutput()
    else:
        return open(str( config_output_tty ), "w")

# @pwndbg.events.stop

parser = argparse.ArgumentParser()
parser.description = "Print out the current register, instruction, and stack context."
parser.add_argument("subcontext", nargs="*", type=str, default=None, help="Submenu to display: 'reg', 'disasm', 'code', 'stack', 'backtrace', and/or 'args'")
@pwndbg.commands.ArgparsedCommand(parser, aliases=['ctx'])
@pwndbg.commands.OnlyWhenRunning
def context(subcontext=None):
    """
    Print out the current register, instruction, and stack context.

    Accepts subcommands 'reg', 'disasm', 'code', 'stack', 'backtrace', and 'args'.
    """
    if subcontext is None:
        subcontext = []
    args = subcontext
    
    if len(args) == 0:
        args = config_context_sections.split()

    args = [a[0] for a in args]

    splited_config_outputs = {
        'r' : config_output_regs,
        'd' : config_output_disasm,
        'a' : config_output_args,
        'c' : config_output_code,
        's' : config_output_stack,
        'b' : config_output_backtrace
    }

    splited_output_queue = {}

    tmp_args = args.copy()
    for tmp_arg in tmp_args:
        if splited_config_outputs[tmp_arg] != 'nosplit':
            func = context_sections.get(tmp_arg, None)
            if func:
                tty_key = str(splited_config_outputs[tmp_arg])
                if tty_key not in splited_output_queue:
                    splited_output_queue[tty_key] = []
                splited_output_queue[tty_key].extend(func())
                args.remove(tmp_arg)

    result = [M.legend()] if args else []

    for arg in args:
        func = context_sections.get(arg, None)
        if func:
            result.extend(func())

    current_tty = os.ttyname(1)
    if current_tty in splited_output_queue:
        result.extend(splited_output_queue[current_tty])
        del splited_output_queue[current_tty]
    if len(result) > 0:
        result.append(pwndbg.ui.banner(""))
    result.extend(context_signal())

    show_context(config_output, result)
    for tty, content in splited_output_queue.items():
        show_context(tty, content)

def context_regs():
    return [pwndbg.ui.banner("registers")] + get_regs()

parser = argparse.ArgumentParser()
parser.description = '''Print out all registers and enhance the information.'''
parser.add_argument("regs", nargs="*", type=str, default=None, help="Registers to be shown")
@pwndbg.commands.ArgparsedCommand(parser)
@pwndbg.commands.OnlyWhenRunning
def regs(regs=None):
    '''Print out all registers and enhance the information.'''
    print('\n'.join(get_regs(*regs)))

pwndbg.config.Parameter('show-flags', False, 'whether to show flags registers')
pwndbg.config.Parameter('show-retaddr-reg', False, 'whether to show return address register')


def get_regs(*regs):
    result = []

    if not regs and pwndbg.config.show_retaddr_reg:
        regs = pwndbg.regs.gpr + (pwndbg.regs.frame, pwndbg.regs.current.stack) + pwndbg.regs.retaddr + (pwndbg.regs.current.pc,)
    elif not regs:
        regs = pwndbg.regs.gpr + (pwndbg.regs.frame, pwndbg.regs.current.stack, pwndbg.regs.current.pc)

    if pwndbg.config.show_flags:
        regs += tuple(pwndbg.regs.flags)

    changed = pwndbg.regs.changed

    for reg in regs:
        if reg is None:
            continue

        if reg not in pwndbg.regs:
            message.warn("Unknown register: %r" % reg)
            continue

        value = pwndbg.regs[reg]

        # Make the register stand out
        regname = C.register(reg.ljust(4).upper())

        # Show a dot next to the register if it changed
        change_marker = "%s" % C.config_register_changed_marker
        m = ' ' * len(change_marker) if reg not in changed else C.register_changed(change_marker)

        if reg in pwndbg.regs.flags:
            desc = C.format_flags(value, pwndbg.regs.flags[reg], pwndbg.regs.last.get(reg, 0))

        else:
            desc = pwndbg.chain.format(value)

        result.append("%s%s %s" % (m, regname, desc))

    return result

pwndbg.config.Parameter('emulate', True, '''
Unicorn emulation of code near the current instruction
''')
code_lines = pwndbg.config.Parameter('context-code-lines', 10, 'number of additional lines to print in the code context')

def context_disasm():
    banner = [pwndbg.ui.banner("disasm")]
    emulate = bool(pwndbg.config.emulate)
    result = pwndbg.commands.nearpc.nearpc(to_string=True, emulate=emulate, lines=code_lines // 2)

    # If we didn't disassemble backward, try to make sure
    # that the amount of screen space taken is roughly constant.
    while len(result) < code_lines + 1:
        result.append('')

    return banner + result

theme.Parameter('highlight-source', True, 'whether to highlight the closest source line')
source_code_lines = pwndbg.config.Parameter('context-source-code-lines',
                                             10,
                                             'number of source code lines to print by the context command')
theme.Parameter('code-prefix', '►', "prefix marker for 'context code' command")

@pwndbg.memoize.reset_on_start
def get_highlight_source(filename):
    # Notice that the code is cached
    with open(filename, encoding='utf-8') as f:
        source = f.read()

    if pwndbg.config.syntax_highlight:
        source = H.syntax_highlight(source, filename)

    source_lines = source.splitlines()
    source_lines = tuple(line.rstrip() for line in source_lines)
    return source_lines

def get_filename_and_formatted_source():
    """
    Returns formatted, lines limited and highlighted source as list
    or if it isn't there - an empty list
    """
    sal = gdb.selected_frame().find_sal()  # gdb.Symtab_and_line

    # Check if source code is available
    if sal.symtab is None:
        return '', []

    # Get the full source code
    closest_line = sal.line
    filename = sal.symtab.fullname()

    try:
        source = get_highlight_source(filename)
    except IOError:
        return '', []

    if not source:
        return '', []

    n = int(source_code_lines)

    # Compute the line range
    start = max(closest_line - 1 - n//2, 0)
    end = min(closest_line - 1 + n//2 + 1, len(source))
    num_width = len(str(end))

    # split the code
    source = source[start:end]

    # Compute the prefix_sign length
    prefix_sign = pwndbg.config.code_prefix
    prefix_width = len(prefix_sign)

    # Format the output
    formatted_source = []
    for line_number, code in enumerate(source, start=start + 1):
        fmt = ' {prefix_sign:{prefix_width}} {line_number:>{num_width}} {code}'
        if pwndbg.config.highlight_source and line_number == closest_line:
            fmt = C.highlight(fmt)

        line = fmt.format(
            prefix_sign=C.prefix(prefix_sign) if line_number == closest_line else '',
            prefix_width=prefix_width,
            line_number=line_number,
            num_width=num_width,
            code=code
        )
        formatted_source.append(line)

    return filename, formatted_source


def context_code():
    filename, formatted_source = get_filename_and_formatted_source()

    # Try getting source from files
    if formatted_source:
        return [pwndbg.ui.banner("Source (code)"), 'In file: %s' % filename] + formatted_source

    # Try getting source from IDA Pro Hex-Rays Decompiler
    if not pwndbg.ida.available():
        return []

    n = int(int(int(source_code_lines) / 2)) # int twice to make it a real int instead of inthook
    # May be None when decompilation failed or user loaded wrong binary in IDA
    code = pwndbg.ida.decompile_context(pwndbg.regs.pc, n)
    
    if code:
        return [pwndbg.ui.banner("Hexrays pseudocode")] + code.splitlines()
    else:
        return []


stack_lines = pwndbg.config.Parameter('context-stack-lines', 8, 'number of lines to print in the stack context')


def context_stack():
    result = [pwndbg.ui.banner("stack")]
    telescope = pwndbg.commands.telescope.telescope(pwndbg.regs.sp, to_string=True, count=stack_lines)
    if telescope:
        if config_stack_reverse:
            telescope.reverse()
        result.extend(telescope)
    return result

backtrace_frame_label = theme.Parameter('backtrace-frame-label', 'f ', 'frame number label for backtrace')


def context_backtrace(frame_count=10, with_banner=True):
    result = []

    if with_banner:
        result.append(pwndbg.ui.banner("backtrace"))

    this_frame    = gdb.selected_frame()
    newest_frame  = this_frame
    oldest_frame  = this_frame

    for i in range(frame_count):
        try:
            candidate = oldest_frame.older()
        except gdb.MemoryError:
            break

        if not candidate:
            break
        oldest_frame = candidate

    for i in range(frame_count):
        candidate = newest_frame.newer()
        if not candidate:
            break
        newest_frame = candidate

    frame = newest_frame
    i     = 0
    bt_prefix = "%s" % B.config_prefix
    while True:

        prefix = bt_prefix if frame == this_frame else ' ' * len(bt_prefix)
        prefix = " %s" % B.prefix(prefix)
        addrsz = B.address(pwndbg.ui.addrsz(frame.pc()))
        symbol = B.symbol(pwndbg.symbol.get(frame.pc()))
        if symbol:
            addrsz = addrsz + ' ' + symbol
        line   = map(str, (prefix, B.frame_label('%s%i' % (backtrace_frame_label, i)), addrsz))
        line   = ' '.join(line)
        result.append(line)

        if frame == oldest_frame:
            break

        frame = frame.older()
        i    += 1
    return result


def context_args(with_banner=True):
    args = pwndbg.arguments.format_args(pwndbg.disasm.one())

    # early exit to skip section if no arg found
    if not args:
        return []

    if with_banner:
        args.insert(0, pwndbg.ui.banner("arguments"))

    return args

last_signal = []


def save_signal(signal):
    global last_signal
    last_signal = result = []

    if isinstance(signal, gdb.ExitedEvent):
        # Booooo old gdb
        if hasattr(signal, 'exit_code'):
            result.append(message.exit('Exited: %r' % signal.exit_code))

    elif isinstance(signal, gdb.SignalEvent):
        msg = 'Program received signal %s' % signal.stop_signal

        if signal.stop_signal == 'SIGSEGV':

            # When users use rr (https://rr-project.org or https://github.com/mozilla/rr)
            # we can't access $_siginfo, so lets just show current pc
            # see also issue 476
            if _is_rr_present():
                msg += ' (current pc: %#x)' % pwndbg.regs.pc
            else:
                try:
                    si_addr = gdb.parse_and_eval("$_siginfo._sifields._sigfault.si_addr")
                    msg += ' (fault address %#x)' % int(si_addr or 0)
                except gdb.error:
                    pass
        result.append(message.signal(msg))

    elif isinstance(signal, gdb.BreakpointEvent):
        for bkpt in signal.breakpoints:
            result.append(message.breakpoint('Breakpoint %s' % (bkpt.location)))

gdb.events.cont.connect(save_signal)
gdb.events.stop.connect(save_signal)
gdb.events.exited.connect(save_signal)


def context_signal():
    return last_signal


context_sections = {
    'r': context_regs,
    'd': context_disasm,
    'a': context_args,
    'c': context_code,
    's': context_stack,
    'b': context_backtrace
}


@pwndbg.memoize.forever
def _is_rr_present():
    """
    Checks whether rr project is present (so someone launched e.g. `rr replay <some-recording>`)
    """

    # this is ugly but I couldn't find a better way to do it
    # feel free to refactor it
    globals_list_literal_str = gdb.execute('python print(list(globals().keys()))', to_string=True)
    interpreter_globals = ast.literal_eval(globals_list_literal_str)

    return 'RRCmd' in interpreter_globals and 'RRWhere' in interpreter_globals
