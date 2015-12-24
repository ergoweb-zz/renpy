# Copyright 2004-2015 Tom Rothamel <pytom@bishoujo.us>
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import renpy.display
import pygame

# The overridden positioning of the mouse.
mouse_pos = None

# The mouse buttons.
mouse_buttons = [ 0, 0, 0 ]

def get_mouse_pos(x, y):
    """
    Called to get the overridden mouse position.
    """

    if mouse_pos is None:
        return x, y

    return mouse_pos

def post(event_type, **kwargs):
    pygame.event.post(pygame.event.Event(event_type, test=True, **kwargs))

def move_mouse(x, y):
    """
    Moves the mouse to x, y.
    """

    global mouse_pos

    pos = (x, y)

    if mouse_pos != pos:
        if mouse_pos:
            rel = (pos[0] - mouse_pos[0], pos[1] - mouse_pos[1])
        else:
            rel = (0, 0)

        post(pygame.MOUSEMOTION, pos=pos, rel=rel, buttons=tuple(mouse_buttons))

    mouse_pos = pos

def press_mouse(button):
    """
    Presses mouse button `button`.
    """

    post(pygame.MOUSEBUTTONDOWN, pos=mouse_pos, button=button)
    mouse_buttons[button - 1] = 1

def release_mouse(button):
    """
    Releases mouse button `button`.
    """
    post(pygame.MOUSEBUTTONUP, pos=mouse_pos, button=button)
    mouse_buttons[button - 1] = 0

def click_mouse(button, x, y):
    """
    Clicks the mouse at x, y
    """

    move_mouse(x, y)
    press_mouse(button)
    release_mouse(button)

class TestNode(object):
    """
    An AST node for a test script.
    """

    def start(self):
        """
        Called once when the node starts execution.

        This is expected to return a state, or None to advance to the next
        node.
        """

    def per_interact(self, state, t):
        """
        Called at the start or restart of an interaction.

        `state`
            The last state that was returned from this node.

        `t`
            The time since start was called.
        """

        return True

    def periodic(self, state, t):
        """
        Called periodically over the course of each interaction.

        `state`
            The last state that was returned from this node.

        `t`
            The time since start was called.
        """

        return state

    def ready(self):
        """
        Returns True if this node is ready to execute, or False otherwise.
        """

        return True

class Click(object):

#     def __init__(self, target):
#         self.target = target

    def start(self):
        return 0

    def per_interact(self, state, t):
        return t

    def periodic(self, state, t):

        if t - state < .5:
            return state

        click_mouse(1, 100, 100)
        return t

# The root node.
node = None # Click()

# The state of the root node.
status = None

# The time the root node started executing.
start_time = None

def periodic(per_interact=False):
    """
    Called periodically by the test code to generate events, if desired.
    """

    global node
    global status
    global start_time

    if node is None:
        return

    now = renpy.display.core.get_time()

    if status is None:
        status = node.start()
        start_time = now

    if status is None:
        node = None
        return

    if per_interact:
        status = node.per_interact(status, now - start_time)

        if status is None:
            node = None
            return

    status = node.periodic(status, now - start_time)

    if status is None:
        node = None
        return

def per_interact():
    periodic(True)

