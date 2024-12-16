# Copyright 2004-2024 Tom Rothamel <pytom@bishoujo.us>
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

# This file contains the AST for the Ren'Py script language. Each class
# here corresponds to a statement in the script language.

# NOTE:
# When updating this file, consider if lint.py or warp.py also need
# updating.

from __future__ import annotations

from typing import Callable, Literal, Never, Optional, Any, TypeAlias

import renpy
from renpy.ast.node import Node
from renpy.ast.python import PyCode

import re
import sys


from renpy.parameter import (
    ParameterInfo,
    ArgumentInfo,
    apply_arguments,
    EMPTY_PARAMETERS,
)

################################################################################
# Utility functions
################################################################################

# The name of the current statement.
current_statement_name: str = "init"


def statement_name(name: str):
    """
    Reports the name of this statement to systems like window auto.
    """

    global current_statement_name
    current_statement_name = name

    for i in renpy.config.statement_callbacks:
        i(name)


def next_node(n: renpy.ast.Node | None):
    """
    Indicates the next node that should be executed. When a statement
    can crash, this should be set as early as possible, so that ignore
    can bring us there.
    """

    renpy.game.context().next_node = n


def probably_side_effect_free(expr: str) -> bool:
    """
    Returns true if an expr probably does not have side effects, and should
    be predicted. Basically, this just whitelists a set of characters that
    doesn't allow for a function call.
    """

    return not ("(" in expr)


def chain_block(block: list[renpy.ast.Node], next: renpy.ast.Node | None) -> None:
    """
    This is called to chain together all of the nodes in a block. Node
    n is chained with node n+1, while the last node is chained with
    next.
    """

    if not block:
        return

    for a, b in zip(block, block[1:]):
        a.chain(b)

    block[-1].chain(next)


def say_menu_with(expression: str | None, callback: Callable[[Any], Any]):
    """
    This handles the with clause of a say or menu statement.
    """

    if expression is not None:
        what = renpy.python.py_eval(expression)
    elif renpy.store.default_transition and renpy.game.preferences.transitions == 2:
        what = renpy.store.default_transition
    else:
        return

    if not what:
        return

    if renpy.game.preferences.transitions:
        callback(what)


def eval_who(who: str | None, fast: bool | None = None) -> Any | None:
    """
    Evaluates the `who` parameter to a say statement.
    """

    if who is None:
        return None

    if 'store.character' in renpy.python.store_dicts:
        rv = renpy.python.store_dicts['store.character'].get(who, None)
    else:
        rv = None

    if rv is None:
        rv = renpy.python.store_dicts['store'].get(who, None)

    if rv is not None:
        return rv

    try:
        return renpy.python.py_eval(who)
    except Exception:
        raise Exception("Sayer '%s' is not defined." % who)


_imspec: TypeAlias = """
tuple[tuple[str, ...], str | None, str | None, list[str], str | None, str | None, list[str]] |
tuple[tuple[str, ...], str | None, str | None, list[str], str | None, str | None] |
tuple[tuple[str, ...], list[str], str | None]
"""


def predict_imspec(imspec: _imspec, scene=False, atl: renpy.atl.RawBlock | None = None):
    """
    Call this to use the given callback to predict the image named
    in imspec.
    """

    if len(imspec) == 7:
        name, expression, tag, at_expr_list, layer, _zorder, _behind = imspec

    elif len(imspec) == 6:
        name, expression, tag, at_expr_list, layer, _zorder = imspec

    else:
        name, at_expr_list, layer = imspec
        tag = None
        expression = None

    if expression:
        try:
            img = renpy.python.py_eval(expression)
            img = renpy.easy.displayable(img)
        except Exception:
            return
    else:
        img = None

    at_list = []
    for i in at_expr_list:
        try:
            at_list.append(renpy.python.py_eval(i))
        except Exception:
            pass

    if atl is not None:
        try:
            at_list.append(renpy.display.transform.ATLTransform(atl))
        except Exception:
            pass

    layer = renpy.exports.default_layer(layer, tag or name, bool(expression))

    if scene:
        renpy.game.context().images.predict_scene(layer)

    renpy.exports.predict_show(name, layer, what=img, tag=tag, at_list=at_list)


def show_imspec(imspec: _imspec, atl: renpy.atl.RawBlock | None = None):

    if len(imspec) == 7:
        name, expression, tag, at_list, layer, zorder, behind = imspec

    elif len(imspec) == 6:
        name, expression, tag, at_list, layer, zorder = imspec
        behind = []

    else:
        name, at_list, layer = imspec
        expression = None
        tag = None
        zorder = None
        behind = []

    if zorder is not None:
        zorder = renpy.python.py_eval(zorder)
    else:
        zorder = None

    if expression is not None:
        expression = renpy.python.py_eval(expression)

        if not renpy.config.old_show_expression:

            if isinstance(expression, str):
                name = expression

            else:

                if tag is None:

                    counter = 0

                    while True:
                        tag = "_show_expression_%d" % counter
                        if not renpy.exports.showing(tag, layer):
                            break

                        counter += 1

                name = tag

        expression = renpy.easy.displayable(expression)

    at_list = [renpy.python.py_eval(i) for i in at_list]

    layer = renpy.exports.default_layer(layer, tag or name, bool(expression) and (tag is None))

    renpy.config.show(name,
                      at_list=at_list,
                      layer=layer,
                      what=expression,
                      zorder=zorder,
                      tag=tag,
                      behind=behind,
                      atl=atl)


def create_store(name: str):
    if name in renpy.config.special_namespaces:
        return

    # Take first two components of dot-joined name
    maybe_special = ".".join(name.split(".")[:2])
    if maybe_special in renpy.config.special_namespaces:
        if not renpy.config.special_namespaces[maybe_special].allow_child_namespaces:
            raise Exception('Creating stores within the {} namespace is not supported.'.format(maybe_special[6:]))

    renpy.python.create_store(name)


class StoreNamespace:
    pure = True
    repeat_at_default_time = False

    def __init__(self, store):
        self.store = store

    def set(self, name: str, value: Any):
        renpy.python.store_dicts[self.store][name] = value

    def set_default(self, name: str, value: Any):
        renpy.python.store_dicts[self.store][name] = value

    def get(self, name: str) -> Any:
        return renpy.python.store_dicts[self.store][name]


def get_namespace(store: str) -> tuple[StoreNamespace, bool]:
    """
    Returns the namespace object for `store`, and a flag that is true if the
    namespace is special, and false if it is a normal store.
    """

    if store in renpy.config.special_namespaces:
        return renpy.config.special_namespaces[store], True

    return StoreNamespace(store), False


################################################################################
# Basic statements
################################################################################

class Say(Node):

    __slots__ = [
        'who',
        'who_fast',
        'what',
        'with_',
        'interact',
        'attributes',
        'arguments',
        'temporary_attributes',
        'rollback',
        'identifier',
        'explicit_identifier',
    ]

    who: str | None
    who_fast: bool
    what: str
    with_: str | None
    interact: bool
    attributes: tuple[str, ...] | None
    arguments: ArgumentInfo | None
    temporary_attributes: tuple[str, ...] | None
    rollback: Literal['normal', 'never', 'force']
    identifier: str | None
    explicit_identifier: bool

    def diff_info(self):
        return (Say, self.who, self.what)

    def __new__(cls, *args, **kwargs):
        self = Node.__new__(cls)
        self.attributes = None
        self.interact = True
        self.arguments = None
        self.temporary_attributes = None
        self.rollback = "normal"
        self.explicit_identifier = False
        return self

    def __init__(
        self,
        loc,
        who,
        what,
        with_,
        interact=True,
        attributes=None,
        arguments=None,
        temporary_attributes=None,
        identifier=None,
    ):

        super(Say, self).__init__(loc)

        if who is not None:
            # True if who is a simple enough expression we can just look it up.
            if re.match(renpy.lexer.word_regexp + r"\s*$", who):
                self.who_fast = True
                self.who = sys.intern(who.strip())
            else:
                self.who_fast = False
                self.who = who

        else:
            self.who = None
            self.who_fast = False

        self.what = what
        self.with_ = with_
        self.interact = interact
        self.arguments = arguments

        # A tuple of attributes that are applied to the character that's
        # speaking, or None to disable this behavior.
        self.attributes = attributes

        # Ditto for temporary attributes.
        self.temporary_attributes = temporary_attributes

        # If given, write in the identifier.
        if identifier is not None:
            self.identifier = identifier
            self.explicit_identifier = True

    def get_code(self, dialogue_filter=None):
        rv = []

        if self.who:
            rv.append(self.who)

        if self.attributes is not None:
            rv.extend(self.attributes)

        if self.temporary_attributes:
            rv.append("@")
            rv.extend(self.temporary_attributes)

        what = self.what
        if dialogue_filter is not None:
            what = dialogue_filter(what)

        rv.append(renpy.translation.encode_say_string(what))

        if not self.interact:
            rv.append("nointeract")

        if getattr(self, "identifier", None) and self.explicit_identifier:
            rv.append("id")
            rv.append(getattr(self, "identifier", None))

        if self.arguments:
            rv.append(self.arguments.get_code())

        # This has to be at the end.
        if self.with_:
            rv.append("with")
            rv.append(self.with_)

        return " ".join(rv)

    def execute(self):

        next_node(self.next)

        try:

            renpy.game.context().say_attributes = self.attributes
            renpy.game.context().temporary_attributes = self.temporary_attributes

            who = eval_who(self.who, self.who_fast)

            stmt_name: str = "say"
            if who is not None:
                stmt_name = getattr(who, "statement_name", "say")

                if callable(stmt_name):
                    stmt_name = stmt_name()

            statement_name(stmt_name)

            if not (
                (who is None) or
                callable(who) or
                isinstance(who, str)
            ):
                raise Exception(f"Sayer {self.who!r} is not a function or string.")

            what = self.what
            if renpy.config.say_menu_text_filter:
                what = renpy.config.say_menu_text_filter(what)

            renpy.store._last_raw_what = what

            if self.arguments is not None:
                args, kwargs = self.arguments.evaluate()
            else:
                args = ()
                kwargs = {}

            kwargs.setdefault("interact", self.interact)

            if getattr(who, "record_say", True):
                renpy.store._last_say_who = self.who
                renpy.store._last_say_what = what
                renpy.store._last_say_args = args
                renpy.store._last_say_kwargs = kwargs

            say_menu_with(self.with_, renpy.game.interface.set_transition)
            renpy.exports.say(who, what, *args, **kwargs)

        finally:
            renpy.game.context().say_attributes = None
            renpy.game.context().temporary_attributes = None
            renpy.store._last_raw_what = ""  # type: ignore

    def predict(self):

        old_attributes = renpy.game.context().say_attributes
        old_temporary_attributes = renpy.game.context().temporary_attributes

        try:

            renpy.game.context().say_attributes = self.attributes
            renpy.game.context().temporary_attributes = self.temporary_attributes

            who = eval_who(self.who, self.who_fast)

            def predict_with(trans):
                renpy.display.predict.displayable(trans(old_widget=None, new_widget=None))

            try:
                say_menu_with(self.with_, predict_with)
            except Exception:
                pass

            what = self.what
            if renpy.config.say_menu_text_filter:
                what = renpy.config.say_menu_text_filter(what)

            renpy.exports.predict_say(who, what)

        finally:
            renpy.game.context().say_attributes = old_attributes
            renpy.game.context().temporary_attributes = old_temporary_attributes

        return [self.next]

    def scry(self):
        rv = Node.scry(self)

        who = eval_who(self.who, self.who_fast)
        rv.who = who
        rv.say = True

        if self.interact:
            renpy.exports.scry_say(who, self.what, rv)
        else:
            rv.interacts = False
            rv.extend_text = renpy.ast.DoesNotExtend

        return rv


# Copy the descriptor.
setattr(Say, "with", Say.with_)


class Init(Node):

    __slots__ = [
        'block',
        'priority',
    ]

    block: list[Node]
    priority: int

    def __init__(self, loc, block, priority):
        super(Init, self).__init__(loc)

        self.block = block
        self.priority = priority

    def get_children(self, f):
        f(self)

        for i in self.block:
            i.get_children(f)

    def get_init(self):
        return self.priority, self.block[0]

    # We handle chaining specially. We want to chain together the nodes in
    # the block, but we want that chain to end in None, and we also want
    # this node to just continue on to the next node in normal execution.
    def chain(self, next):
        self.next = next

        chain_block(self.block, None)

    def execute(self):
        next_node(self.next)
        renpy.execution.not_infinite_loop(60)
        statement_name("init")

    def restructure(self, callback):
        callback(self.block)


class Label(Node):

    translation_relevant = True
    __slots__ = [
        'parameters',
        'block',
        'hide',
    ]

    parameters: ParameterInfo | None
    block: list[Node]
    hide: bool

    def __new__(cls, *args, **kwargs):
        self = Node.__new__(cls)
        self.parameters = None
        self.hide = False
        return self

    def __init__(self, loc, name, block, parameters, hide=False):
        """
        Constructs a new Label node.

        @param name: The name of this label.
        @param block: A (potentially empty) list of nodes making up the
        block associated with this label.
        """

        super(Label, self).__init__(loc)

        self.name = name
        self.block = block
        self.parameters = parameters
        self.hide = hide

    def diff_info(self):
        return (Label, self.name)

    def get_children(self, f):
        f(self)

        for i in self.block:
            i.get_children(f)

    def chain(self, next):

        if self.block:
            self.next = self.block[0]
            chain_block(self.block, next)
        else:
            self.next = next

    def execute(self):
        next_node(self.next)
        statement_name("label")

        renpy.game.context().mark_seen()

        values = apply_arguments(
            self.parameters,
            renpy.store._args,
            renpy.store._kwargs)

        renpy.exports.dynamic(**values)

        renpy.store._args = None
        renpy.store._kwargs = None

        renpy.easy.run_callbacks(
            renpy.config.label_callback, self.name,
            renpy.game.context().last_abnormal)
        renpy.easy.run_callbacks(
            renpy.config.label_callbacks, self.name,
            renpy.game.context().last_abnormal)

    def restructure(self, callback):
        callback(self.block)


class Python(Node):

    __slots__ = [
        'hide',
        'code',
        'store',
    ]

    hide: bool
    code: PyCode
    store: str

    def __new__(cls, *args, **kwargs):
        self = Node.__new__(cls)
        self.store = "store"
        return self

    def __init__(self, loc, python_code, hide=False, store="store"):
        """
        @param code: A PyCode object.

        @param hide: If True, the code will be executed with its
        own local dictionary.
        """

        super(Python, self).__init__(loc)

        self.hide = hide

        if hide:
            self.code = PyCode(python_code, loc=loc, mode='hide')
        else:
            self.code = PyCode(python_code, loc=loc, mode='exec')

        self.store = store

    def diff_info(self):
        return (Python, self.code.source)

    def early_execute(self):
        renpy.python.create_store(self.store)

    def execute(self):
        next_node(self.next)
        statement_name("python")

        try:
            renpy.python.py_exec_bytecode(
                self.code.bytecode,
                self.hide,
                store=self.store)
        finally:
            if not renpy.game.context().init_phase:
                for i in renpy.config.python_callbacks:
                    i()

    def scry(self):
        rv = Node.scry(self)
        rv.interacts = True
        return rv


class EarlyPython(Node):

    __slots__ = [
        'hide',
        'code',
        'store',
    ]

    hide: bool
    code: PyCode
    store: str

    def __new__(cls, *args, **kwargs):
        self = Node.__new__(cls)
        self.store = "store"
        return self

    def __init__(self, loc, python_code, hide=False, store="store"):
        """
        @param code: A PyCode object.

        @param hide: If True, the code will be executed with its
        own local dictionary.
        """

        super(EarlyPython, self).__init__(loc)

        self.hide = hide

        if hide:
            self.code = PyCode(python_code, loc=loc, mode='hide')
        else:
            self.code = PyCode(python_code, loc=loc, mode='exec')

        self.store = store

    def diff_info(self):
        return (EarlyPython, self.code.source)

    def execute(self):
        next_node(self.next)
        renpy.execution.not_infinite_loop(60)
        statement_name("python early")

    def early_execute(self):
        renpy.python.create_store(self.store)

        if self.code.bytecode:
            renpy.python.py_exec_bytecode(
                self.code.bytecode,
                self.hide,
                store=self.store)


class Image(Node):

    __slots__ = [
        'imgname',
        'code',
        'atl',
    ]

    imgname: tuple[str, ...]
    code: PyCode | None
    atl: renpy.atl.RawBlock | None

    def __init__(self, loc, name, expr=None, atl=None):
        """
        @param name: The name of the image being defined.

        @param expr: An expression yielding a Displayable that is
        assigned to the image.
        """

        super(Image, self).__init__(loc)

        self.imgname = name

        if expr:
            self.code = PyCode(expr, loc=loc, mode='eval')
            self.atl = None
        else:
            self.code = None
            self.atl = atl

    def diff_info(self):
        return (Image, tuple(self.imgname))

    def execute(self):

        # Note: We should always check that self.code is None before
        # accessing self.atl, as self.atl may not always exist.

        next_node(self.next)
        statement_name("image")

        if self.code is not None:
            img = renpy.python.py_eval_bytecode(self.code.bytecode)
        else:
            img = renpy.display.motion.ATLTransform(self.atl)

        renpy.exports.image(self.imgname, img)

    def analyze(self):
        if getattr(self, 'atl', None) is not None:
            # ATL images must participate with the game defined
            # constant names. So, we pass empty parameters to enable it.
            self.atl.analyze(EMPTY_PARAMETERS)


class Transform(Node):

    __slots__ = [
        # The name of the store this transform is stored in.
        'store',

        # The name of the transform.
        'varname',

        # The block of ATL associated with the transform.
        'atl',

        # The parameters associated with the transform, if any.
        'parameters',
    ]

    store: str
    varname: str
    atl: renpy.atl.RawBlock
    parameters: ParameterInfo

    default_parameters = EMPTY_PARAMETERS

    def __new__(cls, *args, **kwargs):
        self = Node.__new__(cls)
        self.store = 'store'
        return self

    def __init__(self, loc, store, name, atl, parameters=default_parameters):

        super(Transform, self).__init__(loc)

        self.store = store
        self.varname = name
        self.atl = atl
        self.parameters = parameters

    def diff_info(self):
        return (Transform, self.store, self.varname)

    def early_execute(self):
        create_store(self.store)

    def execute(self):

        next_node(self.next)
        statement_name("transform")

        parameters = getattr(self, "parameters", None)

        if parameters is None:
            parameters = Transform.default_parameters

        trans = renpy.display.motion.ATLTransform(self.atl, parameters=parameters)
        renpy.dump.transforms.append((self.varname, self.filename, self.linenumber))
        renpy.exports.pure(self.varname)

        ns, _special = get_namespace(self.store)
        ns.set(self.varname, trans)

    def analyze(self):

        parameters = getattr(self, "parameters", None)

        if parameters is None:
            parameters = Transform.default_parameters

        self.atl.analyze(parameters)


class Show(Node):

    __slots__ = [
        'imspec',
        'atl',
    ]

    imspec: _imspec
    atl: renpy.atl.RawBlock | None

    warp = True

    def __init__(self, loc, imspec, atl=None):
        """
        @param imspec: A triple consisting of an image name (itself a
        tuple of strings), a list of at expressions, and a layer.
        """

        super(Show, self).__init__(loc)

        self.imspec = imspec
        self.atl = atl

    def diff_info(self):
        return (Show, tuple(self.imspec[0]))

    def execute(self):
        next_node(self.next)
        statement_name("show")

        show_imspec(self.imspec, atl=getattr(self, "atl", None))

    def predict(self):
        predict_imspec(self.imspec, atl=getattr(self, "atl", None))
        return [self.next]

    def analyze(self):
        if getattr(self, 'atl', None) is not None:
            # ATL block defined for show, scene or show layer statements
            # must participate with the game defined constant names.
            # So, we pass empty parameters to enable it.
            self.atl.analyze(EMPTY_PARAMETERS)


class ShowLayer(Node):

    warp = True

    __slots__ = [
        'layer',
        'at_list',
        'atl',
    ]

    layer: str
    at_list: list[str]
    atl: renpy.atl.RawBlock | None

    def __init__(self, loc, layer, at_list, atl):
        super(ShowLayer, self).__init__(loc)

        self.layer = layer
        self.at_list = at_list
        self.atl = atl

    def diff_info(self):
        return (ShowLayer, self.layer)

    def execute(self):
        next_node(self.next)
        statement_name("show layer")

        at_list = [renpy.python.py_eval(i) for i in self.at_list]

        if self.atl is not None:
            atl = renpy.display.motion.ATLTransform(self.atl)
            at_list.append(atl)

        renpy.exports.layer_at_list(at_list, layer=self.layer)

    def predict(self):
        return [self.next]

    def analyze(self):
        if self.atl is not None:
            self.atl.analyze(EMPTY_PARAMETERS)


class Camera(Node):

    warp = True

    __slots__ = [
        'layer',
        'at_list',
        'atl',
    ]

    layer: str
    at_list: list[str]
    atl: renpy.atl.RawBlock | None

    def __init__(self, loc, layer, at_list, atl):
        super(Camera, self).__init__(loc)

        self.layer = layer
        self.at_list = at_list
        self.atl = atl

    def diff_info(self):
        return (Camera, self.layer)

    def execute(self):
        next_node(self.next)
        statement_name("show layer")

        at_list = [renpy.python.py_eval(i) for i in self.at_list]

        if self.atl is not None:
            atl = renpy.display.motion.ATLTransform(self.atl)
            at_list.append(atl)

        renpy.exports.layer_at_list(at_list, layer=self.layer, camera=True)

    def predict(self):
        return [self.next]

    def analyze(self):
        if self.atl is not None:
            self.atl.analyze(EMPTY_PARAMETERS)


class Scene(Node):

    __slots__ = [
        'imspec',
        'layer',
        'atl',
    ]

    imspec: _imspec
    layer: str
    atl: renpy.atl.RawBlock | None

    warp = True

    def __init__(self, loc, imgspec, layer, atl=None):
        """
        @param imspec: A triple consisting of an image name (itself a
        tuple of strings), a list of at expressions, and a layer, or
        None to not have this scene statement also display an image.
        """

        super(Scene, self).__init__(loc)

        self.imspec = imgspec
        self.layer = layer
        self.atl = atl

    def diff_info(self):

        if self.imspec:
            data = tuple(self.imspec[0])
        else:
            data = None

        return (Scene, data)

    def execute(self):

        next_node(self.next)
        statement_name("scene")

        renpy.config.scene(self.layer)

        if self.imspec:
            show_imspec(self.imspec, atl=getattr(self, "atl", None))

    def predict(self):

        if self.imspec:
            predict_imspec(self.imspec, atl=getattr(self, "atl", None), scene=True)

        return [self.next]

    def analyze(self):
        if getattr(self, 'atl', None) is not None:
            self.atl.analyze(EMPTY_PARAMETERS)


class Hide(Node):

    __slots__ = [
        'imspec',
    ]

    imspec: _imspec

    warp = True

    def __init__(self, loc, imgspec):
        """
        @param imspec: A triple consisting of an image name (itself a
        tuple of strings), a list of at expressions, and a list of
        with expressions.
        """

        super(Hide, self).__init__(loc)

        self.imspec = imgspec

    def diff_info(self):
        return (Hide, tuple(self.imspec[0]))

    def predict(self):

        if len(self.imspec) == 7:
            name, _expression, tag, _at_list, layer, _zorder, _behind = self.imspec

        elif len(self.imspec) == 6:
            name, _expression, tag, _at_list, layer, _zorder = self.imspec
            _behind = None
        else:
            name, _at_list, layer = self.imspec
            tag = None
            _expression = None
            _zorder = None
            _behind = None

        if tag is None:
            tag = name[0]

        layer = renpy.exports.default_layer(layer, tag)

        renpy.game.context().images.predict_hide(layer, (tag,))

        return [self.next]

    def execute(self):

        next_node(self.next)
        statement_name("hide")

        if len(self.imspec) == 7:
            name, _expression, tag, _at_list, layer, _zorder, _behind = self.imspec
        elif len(self.imspec) == 6:
            name, _expression, tag, _at_list, layer, _zorder = self.imspec
        else:
            name, _at_list, layer = self.imspec
            _expression = None
            tag = None
            _zorder = 0

        layer = renpy.exports.default_layer(layer, tag or name)

        renpy.config.hide(tag or name, layer)


class With(Node):

    __slots__ = [
        'expr',
        'paired',
    ]

    expr: str
    paired: str | None

    def __new__(cls, *args, **kwargs):
        self = Node.__new__(cls)
        self.paired = None
        return self

    def __init__(self, loc, expr, paired=None):
        """
        @param expr: An expression giving a transition or None.
        """

        super(With, self).__init__(loc)
        self.expr = expr
        self.paired = paired

    def diff_info(self):
        return (With, self.expr)

    def execute(self):

        next_node(self.next)
        statement_name("with")

        trans = renpy.python.py_eval(self.expr)

        if self.paired is not None:
            paired = renpy.python.py_eval(self.paired)
        else:
            paired = None

        renpy.exports.with_statement(trans, paired=paired)

    def predict(self):

        try:
            trans = renpy.python.py_eval(self.expr)

            if trans:
                renpy.display.predict.displayable(trans(old_widget=None, new_widget=None))

        except Exception:
            pass

        return [self.next]


class Call(Node):

    __slots__ = [
        'label',
        'arguments',
        'expression',
        'global_label',
    ]

    label: str
    arguments: ArgumentInfo | None
    expression: bool
    global_label: str

    def __new__(cls, *args, **kwargs):
        self = Node.__new__(cls)
        self.arguments = None
        self.global_label = ""
        return self

    def __init__(self, loc, label, expression, arguments, global_label=""):

        super(Call, self).__init__(loc)
        self.label = label
        self.expression = expression
        self.arguments = arguments
        self.global_label = global_label

    def diff_info(self):
        return (Call, self.label, self.expression)

    def execute(self):

        statement_name("call")

        label = self.label
        if self.expression:
            label = renpy.python.py_eval(label)

            if isinstance(label, str) and label.startswith("."):
                label = self.global_label + label

        rv = renpy.game.context().call(label, return_site=self.next.name)
        next_node(rv)
        renpy.game.context().abnormal = True

        if self.arguments:
            args, kwargs = self.arguments.evaluate()
            renpy.store._args = args
            renpy.store._kwargs = kwargs
        else:
            renpy.store._args = None
            renpy.store._kwargs = None

    def predict(self):

        label = self.label

        if self.expression:

            if not probably_side_effect_free(label):
                return []

            try:
                label = renpy.python.py_eval(label)
            except Exception:
                return []

            if isinstance(label, str) and label.startswith("."):
                label = self.global_label + label

            if not renpy.game.script.has_label(label):
                return []

        return [renpy.game.context().predict_call(label, self.next.name)]

    def scry(self):
        rv = Node.scry(self)
        rv._next = None
        return rv


class Return(Node):

    __slots__ = ['expression']

    expression: str | None

    def __new__(cls, *args, **kwargs):
        self = Node.__new__(cls)
        self.expression = None
        return self

    def __init__(self, loc, expression):
        super(Return, self).__init__(loc)
        self.expression = expression

    def diff_info(self):
        return (Return, )

    # We don't care what the next node is.
    def chain(self, next):
        self.next = None

    def execute(self):

        statement_name("return")

        if self.expression:
            renpy.store._return = renpy.python.py_eval(self.expression)
        else:
            renpy.store._return = None

        ctx = renpy.game.context()

        if renpy.game.context().init_phase:
            if len(ctx.return_stack) == 0:

                if renpy.config.developer:
                    raise Exception("Unexpected return during the init phase.")

                return

        next_node(renpy.game.context().lookup_return(pop=True))
        renpy.game.context().pop_dynamic()

    def predict(self):

        return [renpy.game.context().predict_return()]

    def scry(self):
        rv = Node.scry(self)
        rv._next = None
        return rv


class Menu(Node):

    translation_relevant = True

    __slots__ = [
        'items',
        'set',
        'with_',
        'has_caption',
        'arguments',
        'item_arguments',
        'rollback',
        'statement_start',  # type: ignore
    ]

    items: list[tuple[str, str, list[Node] | None]]
    set: str | None
    with_: str | None
    has_caption: bool
    arguments: ArgumentInfo | None
    item_arguments: list[ArgumentInfo | None] | None
    rollback: Literal["normal", "never", "force"]
    statement_start: Node

    def __new__(cls, *args, **kwargs):
        self = Node.__new__(cls)
        self.has_caption = False
        self.arguments = None
        self.item_arguments = None
        self.rollback = "force"
        return self

    def __init__(self, loc, items, set, with_, has_caption, arguments, item_arguments):
        super(Menu, self).__init__(loc)

        self.items = items
        self.set = set
        self.with_ = with_
        self.has_caption = has_caption
        self.arguments = arguments
        self.item_arguments = item_arguments

    def diff_info(self):
        return (Menu, )

    def get_children(self, f):
        f(self)

        for _label, _condition, block in self.items:
            if block:
                for i in block:
                    i.get_children(f)

    # Blocks of statements in a choice continue after the menu.
    def chain(self, next):

        self.next = next

        for (_label, _condition, block) in self.items:
            if block:
                chain_block(block, next)

    def replace_next(self, old, new):
        Node.replace_next(self, old, new)

        for _label, _condition, block in self.items:
            if block and (block[0] is old):
                block.insert(0, new)

    def execute(self):

        next_node(self.next)

        if self.arguments is not None:
            args, kwargs = self.arguments.evaluate()
        else:
            args = kwargs = None

        name = "menu"

        if kwargs is not None and kwargs.get('nvl') is True:
            name = "menu-nvl"

        if self.has_caption or renpy.config.choice_empty_window:
            name += "-with-caption"

        statement_name(name)

        choices = []
        narration = []
        item_arguments = []

        for i, (label, condition, block) in enumerate(self.items):

            if renpy.config.say_menu_text_filter:
                label = renpy.config.say_menu_text_filter(label)

            has_item = False

            if block is None:
                if renpy.config.narrator_menu and label:
                    narration.append(label)
                else:
                    choices.append((label, condition, None))
                    has_item = True

            else:
                choices.append((label, condition, i))
                has_item = True

                next_node(block[0])

            if has_item:
                if self.item_arguments and (self.item_arguments[i] is not None):
                    item_arguments.append(self.item_arguments[i].evaluate())
                else:
                    item_arguments.append(((), {}))

        if narration:
            renpy.exports.say(None, "\n".join(narration), interact=False)

        say_menu_with(self.with_, renpy.game.interface.set_transition)

        choice = renpy.exports.menu(choices, self.set, args, kwargs, item_arguments)

        if choice is not None:
            next_node(self.items[choice][2][0])
        else:
            next_node(self.next)

    def predict(self):
        rv = []

        def predict_with(trans):
            renpy.display.predict.displayable(trans(old_widget=None, new_widget=None))

        say_menu_with(self.with_, predict_with)

        renpy.store.predict_menu()

        for _label, _condition, block in self.items:
            if block:
                rv.append(block[0])

        return rv

    def scry(self):
        rv = Node.scry(self)
        rv._next = None
        rv.interacts = True
        if self.has_caption:
            rv.menu_with_caption = True
        return rv

    def restructure(self, callback):
        for _label, _condition, block in self.items:
            if block is not None:
                callback(block)


setattr(Menu, "with", Menu.with_)  # type: ignore


# Goto is considered harmful. So we decided to name it "jump"
# instead.
class Jump(Node):

    __slots__ = [
        'target',
        'expression',
        'global_label',
    ]

    target: str
    expression: bool
    global_label: str

    def __new__(cls, *args, **kwargs):
        self = Node.__new__(cls)
        self.global_label = ""
        return self

    def __init__(self, loc, target, expression, global_label=""):
        super(Jump, self).__init__(loc)

        self.target = target
        self.expression = expression
        self.global_label = global_label

    def diff_info(self):
        return (Jump, self.target, self.expression)

    # We don't care what our next node is.
    def chain(self, next):
        self.next = None

    def execute(self):

        statement_name("jump")

        target = self.target
        if self.expression:
            target = renpy.python.py_eval(target)

            if isinstance(target, str) and target.startswith("."):
                target = self.global_label + target

        rv = renpy.game.script.lookup(target)
        renpy.game.context().abnormal = True

        next_node(rv)

    def predict(self):

        label = self.target

        if self.expression:

            if not probably_side_effect_free(label):
                return []

            try:
                label = renpy.python.py_eval(label)
            except Exception:
                return []

            if isinstance(label, str) and label.startswith("."):
                label = self.global_label + label

            if not renpy.game.script.has_label(label):
                return []

        return [renpy.game.script.lookup(label)]

    def scry(self):
        rv = Node.scry(self)
        if self.expression:
            rv._next = None
        else:
            rv._next = renpy.game.script.lookup(self.target)

        return rv


# GNDN
class Pass(Node):

    __slots__ = []

    def diff_info(self):
        return (Pass, )

    def execute(self):
        next_node(self.next)
        statement_name("pass")


class While(Node):

    __slots__ = [
        'condition',
        'block',
    ]

    condition: str
    block: list[Node]

    def __init__(self, loc, condition, block):
        super(While, self).__init__(loc)

        self.condition = condition
        self.block = block

    def diff_info(self):
        return (While, self.condition)

    def get_children(self, f):
        f(self)

        for i in self.block:
            i.get_children(f)

    def chain(self, next):
        self.next = next
        chain_block(self.block, self)

    def replace_next(self, old, new):
        Node.replace_next(self, old, new)

        if self.block and (self.block[0] is old):
            self.block.insert(0, new)

    def execute(self):

        next_node(self.next)
        statement_name("while")

        if renpy.python.py_eval(self.condition):
            next_node(self.block[0])

    def predict(self):
        return [self.block[0], self.next]

    def scry(self):
        rv = Node.scry(self)
        rv._next = None
        return rv

    def restructure(self, callback):
        callback(self.block)


class If(Node):

    __slots__ = ['entries']

    entries: list[tuple[str, list[Node]]]

    def __init__(self, loc, entries):
        """
        @param entries: A list of (condition, block) tuples.
        """

        super(If, self).__init__(loc)

        self.entries = entries

    def diff_info(self):
        return (If, )

    def get_children(self, f):
        f(self)

        for _condition, block in self.entries:
            for i in block:
                i.get_children(f)

    def chain(self, next):
        self.next = next

        for _condition, block in self.entries:
            chain_block(block, next)

    def replace_next(self, old, new):
        Node.replace_next(self, old, new)

        for _condition, block in self.entries:
            if (block) and (block[0] is old):
                block.insert(0, new)

    def execute(self):

        next_node(self.next)
        statement_name("if")

        for condition, block in self.entries:
            if renpy.python.py_eval(condition):
                next_node(block[0])
                return

    def predict(self):

        return [block[0] for _condition, block in self.entries] + \
               [self.next]

    def scry(self):
        rv = Node.scry(self)
        rv._next = None
        return rv

    def restructure(self, callback):
        for _condition, block in self.entries:
            callback(block)


class UserStatement(Node):

    __slots__ = [
        'line',
        'parsed',
        'block',
        'translatable',
        'code_block',
        'translation_relevant',
        'rollback',
        'subparses',
        'init_priority',
        'atl',
    ]

    line: str
    parsed: Any
    block: list[Any]
    translatable: bool
    code_block: list[Node] | None
    translation_relevant: bool
    rollback: Literal['normal', 'never', 'force']
    subparses: list[renpy.lexer.SubParse]
    init_priority: int
    atl: renpy.atl.RawBlock | None

    def __new__(cls, *args, **kwargs):
        self = Node.__new__(cls)
        self.block = []
        self.code_block = None
        self.translatable = False
        self.translation_relevant = False
        self.rollback = "normal"
        self.subparses = []
        self.init_priority = 0
        self.atl = None
        return self

    def __init__(self, loc, line, block, parsed):

        super(UserStatement, self).__init__(loc)
        self.code_block = None
        self.parsed = parsed
        self.line = line
        self.block = block
        self.subparses = []
        self.init_priority = 0

        self.name = self.call("label")
        self.rollback = renpy.statements.get("rollback", self.parsed) or "normal"

    def __repr__(self):
        return "<UserStatement {!r}>".format(self.line)

    def get_children(self, f):
        f(self)

        if self.code_block is not None:
            for i in self.code_block:
                i.get_children(f)

        for i in self.subparses:
            for j in i.block:
                j.get_children(f)

    def chain(self, next):
        self.next = next

        if self.code_block is not None:
            chain_block(self.code_block, next)

        for i in self.subparses:
            chain_block(i.block, next)

    def replace_next(self, old, new):
        Node.replace_next(self, old, new)

        if (self.code_block) and (self.code_block[0] is old):
            self.code_block.insert(0, new)

        for i in self.subparses:
            if i.block[0] is old:
                i.block.insert(0, new)

    def restructure(self, callback):
        if self.code_block:
            callback(self.code_block)

        for i in self.subparses:
            callback(i.block)

    def diff_info(self):
        return (UserStatement, self.line)

    def call(self, method, *args, **kwargs):

        parsed = self.parsed

        if parsed is None:
            parsed = renpy.statements.parse(self, self.line, self.block)
            self.parsed = parsed

        return renpy.statements.call(method, parsed, *args, **kwargs)

    def execute_init(self):
        self.call("execute_init")

        if renpy.statements.get("init", self.parsed):
            self.init_priority = 1

        if renpy.statements.get("execute_default", self.parsed):
            renpy.ast.default_statements.append(self)

    def get_init(self):
        return self.init_priority, self.execute_init

    def execute(self):
        next_node(self.get_next())
        statement_name(self.get_name())

        if self.atl is not None:
            self.call("execute", atl=renpy.display.transform.ATLTransform(self.atl))
        else:
            self.call("execute")

    def execute_default(self, start):
        self.call("execute_default")

    def predict(self):
        predictions = self.call("predict")

        if predictions is not None:
            for i in predictions:
                renpy.easy.predict(i)

        if self.atl is not None:
            renpy.display.predict.displayable(renpy.display.transform.ATLTransform(self.atl))

        if self.parsed and renpy.statements.get("predict_all", self.parsed):
            return [i.block[0] for i in self.subparses] + [self.next]

        if self.next:
            next_label = self.next.name
        else:
            next_label = None

        next_list = self.call("predict_next", next_label)

        if next_list is not None:
            nexts = [renpy.game.script.lookup_or_none(i) for i in next_list if i is not None]
            return [i for i in nexts if i is not None]

        return [self.next]

    def get_name(self):
        parsed = self.parsed

        if parsed is None:
            parsed = renpy.statements.parse(self, self.line, self.block)
            self.parsed = parsed

        return renpy.statements.get_name(parsed)

    def get_next(self):

        if self.code_block and len(self.code_block):
            rv = self.call("next", self.code_block[0].name)
        else:
            rv = self.call("next")

        if rv is not None:
            return renpy.game.script.lookup(rv)
        else:
            return self.next

    def scry(self):
        rv = Node.scry(self)
        rv._next = self.get_next()
        self.call("scry", rv)
        return rv

    def get_code(self, dialogue_filter=None):
        return self.line

    def can_warp(self):

        if self.call("warp"):
            return True

        return False

    def reachable(self, is_reachable):
        """
        This is used by lint to find statements reachable from or through
        this statement.
        """

        rv = self.call(
            "reachable",
            is_reachable,
            self.name,
            self.next.name if self.next is not None else None,
            self.code_block[0].name if self.code_block else None,
        )

        if rv is None:

            rv = set()

            if self.call("label"):
                rv.add(self.name)
                is_reachable = True

            if is_reachable:

                if self.code_block:
                    rv.add(self.code_block[0].name)

                for i in self.subparses:
                    if i.block:
                        rv.add(i.block[0].name)

                if self.next:
                    rv.add(self.next.name)

        return rv

    def analyze(self):
        if self.atl is not None:
            self.atl.analyze(EMPTY_PARAMETERS)


class PostUserStatement(Node):

    __slots__ = [
        'parent',
    ]

    parent: UserStatement

    def __init__(self, loc, parent):

        super(PostUserStatement, self).__init__(loc)
        self.parent = parent

        self.name = self.parent.call("post_label")

    def __repr__(self):
        return "<PostUserStatement {!r}>".format(self.parent.line)

    def diff_info(self):
        return (PostUserStatement, self.parent.line)

    def execute(self):
        next_node(self.next)
        statement_name("post " + self.parent.get_name())

        self.parent.call("post_execute")


class Define(Node):

    __slots__ = [
        'varname',
        'code',
        'store',
        'operator',
        'index',
    ]

    varname: str
    code: PyCode
    store: str
    operator: str
    index: PyCode | None

    def __new__(cls, *args, **kwargs):
        self = Node.__new__(cls)
        self.store = 'store'
        self.operator = '='
        self.index = None
        return self

    def __init__(self, loc, store, name, index, operator, expr):
        super(Define, self).__init__(loc)

        self.store = store
        self.varname = name

        if index is not None:
            self.index = PyCode(index, loc=loc, mode='eval')
        else:
            self.index = None

        self.operator = operator
        self.code = PyCode(expr, loc=loc, mode='eval')

    def diff_info(self):
        return (Define, self.store, self.varname)

    def early_execute(self):
        create_store(self.store)

        if self.operator != "=":
            return

        if self.index is not None:
            return

        if self.store == "store.config" and self.varname in renpy.ast.EARLY_CONFIG:
            value = renpy.python.py_eval_bytecode(self.code.bytecode)
            setattr(renpy.config, self.varname, value)

    def execute(self):

        next_node(self.next)
        statement_name("define")

        renpy.ast.define_statements.append(self)

        if self.store == 'store':
            renpy.dump.definitions.append((self.varname, self.filename, self.linenumber))
        else:
            renpy.dump.definitions.append((self.store[6:] + "." + self.varname, self.filename, self.linenumber))

        if self.operator == "=" and self.index is None:
            ns, _special = get_namespace(self.store)
            if getattr(ns, "pure", True):
                renpy.exports.pure(self.store + "." + self.varname)

        self.set()

    def redefine(self, stores):

        if self.store not in stores:
            return

        self.set()

    def set(self):

        key = None
        new = None

        value = renpy.python.py_eval_bytecode(self.code.bytecode)
        ns, _special = get_namespace(self.store)

        if (self.index is None) and (self.operator == "="):
            ns.set(self.varname, value)
            return

        base = ns.get(self.varname)
        old = base

        if self.index:
            key = renpy.python.py_eval_bytecode(self.index.bytecode)

            if self.operator != "=":
                old = base[key]

        if self.operator == "=":
            new = value
        elif self.operator == "+=":
            new = old + value
        elif self.operator == "|=":
            new = old | value

        if self.index:
            base[key] = new
        else:
            ns.set(self.varname, new)


class Default(Node):

    __slots__ = [
        'varname',
        'code',
        'store',
    ]

    varname: str
    code: PyCode
    store: str

    def __new__(cls, *args, **kwargs):
        self = Node.__new__(cls)
        self.store = 'store'
        return self

    def __init__(self, loc, store, name, expr):

        super(Default, self).__init__(loc)

        self.store = store
        self.varname = name
        self.code = PyCode(expr, loc=loc, mode='eval')

    def diff_info(self):
        return (Default, self.store, self.varname)

    def early_execute(self):
        create_store(self.store)

    def execute(self):

        next_node(self.next)
        statement_name("default")

        ns, special = get_namespace(self.store)

        if special:
            value = renpy.python.py_eval_bytecode(self.code.bytecode)
            ns.set_default(self.varname, value)

            if getattr(ns, "repeat_at_default_time", False):
                renpy.ast.default_statements.append(self)

            return

        renpy.ast.default_statements.append(self)

        if self.store == 'store':
            renpy.dump.definitions.append((self.varname, self.filename, self.linenumber))
        else:
            renpy.dump.definitions.append((self.store[6:] + "." + self.varname, self.filename, self.linenumber))

    def execute_default(self, start):

        # Handle special namespaces.
        ns, special = get_namespace(self.store)

        if special:
            value = renpy.python.py_eval_bytecode(self.code.bytecode)
            ns.set_default(self.varname, value)
            return

        # Handle normal namespaces.
        d = renpy.python.store_dicts[self.store]

        defaults_set = d.get("_defaults_set", None)

        if defaults_set is None:
            d["_defaults_set"] = defaults_set = renpy.revertable.RevertableSet()
            d.ever_been_changed.add("_defaults_set")

        if self.varname in defaults_set:
            if start and renpy.config.developer:
                raise Exception("{}.{} is being given a default a second time.".format(self.store, self.varname))
            return

        # do the variable shadowing if not in this case, for compatibility reasons
        if start and (renpy.config.developer is True):
            fullname = '.'.join((self.store, self.varname))
            if fullname in renpy.python.store_dicts:
                raise Exception("{} is being given a default, but a store with that name already exists.".format(fullname))

        if start or (self.varname not in d.ever_been_changed):
            d[self.varname] = renpy.python.py_eval_bytecode(self.code.bytecode)

        d.ever_been_changed.add(self.varname)

        defaults_set.add(self.varname)

    def report_traceback(self, name, last):
        return [(self.filename, self.linenumber, name, None)]


class Screen(Node):

    __slots__ = [
        'screen',
    ]

    screen: renpy.sl2.slast.SLScreen

    def __init__(self, loc, screen):
        """
        @param screen: The screen object being defined.
        In SL1, an instance of screenlang.ScreenLangScreen.
        In SL2, an instance of sl2.slast.SLScreen.
        """

        super(Screen, self).__init__(loc)

        self.screen = screen

    def diff_info(self):
        return (Screen, self.screen.name)

    def execute(self):
        next_node(self.next)
        statement_name("screen")

        self.screen.define((self.filename, self.linenumber))
        renpy.dump.screens.append((self.screen.name, self.filename, self.linenumber))


class Style(Node):

    __slots__ = [
        'style_name',
        'parent',
        'properties',
        'clear',
        'take',
        'delattr',
        'variant',
    ]

    style_name: str
    parent: str | None
    properties: dict[str, str]
    clear: bool
    take: str | None
    delattr: list[str]
    variant: str | None

    def __init__(self, loc, name):
        """
        `name`
            The name of the style to define.
        """

        super(Style, self).__init__(loc)

        self.style_name = name

        # The parent of this style.
        self.parent = None

        # Properties.
        self.properties = {}

        # Should we clear the style?
        self.clear = False

        # Should we take properties from another style?
        self.take = None

        # A list of attributes we should delete from this style.
        self.delattr = []

        # If not none, an expression for the variant.
        self.variant = None

    def diff_info(self):
        return (Style, self.style_name)

    def apply(self):
        if self.variant is not None:
            variant = renpy.python.py_eval(self.variant)
            if not renpy.exports.variant(variant):
                return

        s = renpy.style.get_or_create_style(self.style_name)

        if self.clear:
            s.clear()

        if self.parent is not None:
            s.set_parent(self.parent)

        if self.take is not None:
            s.take(self.take)

        for i in self.delattr:
            s.delattr(i)

        if self.properties:
            properties = {}

            for name, expr in self.properties.items():

                value = renpy.python.py_eval(expr)

                if name == "properties":
                    properties.update(value)
                else:
                    properties[name] = value

            s.add_properties(properties)

    def execute(self):
        next_node(self.next)
        statement_name("style")

        if renpy.config.defer_styles and renpy.game.context().init_phase:
            renpy.translation.deferred_styles.append(self)
            return

        self.apply()


class Testcase(Node):

    __slots__ = [
        'label',
        'test',
    ]

    label: str
    test: renpy.test.testast.Block

    def __init__(self, loc, label, test):
        super(Testcase, self).__init__(loc)

        self.label = label
        self.test = test

    def diff_info(self):
        return (Testcase, self.label)

    def execute(self):
        next_node(self.next)
        statement_name("testcase")

        renpy.test.testexecution.testcases[self.label] = self.test


class RPY(Node):
    __slots__ = [
        "rest"
    ]

    rest: tuple[str, ...]

    def __init__(self, loc, rest):
        super(RPY, self).__init__(loc)

        self.rest = rest

    def diff_info(self):
        return (RPY, self.rest)

    def execute(self):
        next_node(self.next)
        statement_name("rpy")

        # rpy python is run in Script.finish_load.

    def get_code(self, dialogue_filter=None):
        return "rpy " + " ".join(self.rest)

################################################################################
# Translations
################################################################################


class Translate(Node):
    """
    A translation block, produced either by explicit translation statements
    or implicit translation blocks.

    If language is None, when executed this transfers control to the translate
    statement in the current language, if any, and otherwise runs the block.
    If language is not None, causes an error to occur if control reaches this
    statement.

    When control normally leaves a translate statement, in any language, it
    goes to the end of the translate statement in the None language.
    """

    rollback = "never"

    translation_relevant = True

    __slots__ = [
        "identifier",
        "alternate",
        "language",
        "block",
        "after",
    ]

    identifier: str | None
    alternate: str | None
    language: str | None
    block: list[Node]
    after: Node | None

    def __init__(self, loc, identifier, language, block, alternate=None):
        super(Translate, self).__init__(loc)

        self.identifier = identifier
        self.alternate = alternate
        self.language = language
        self.block = block

    def diff_info(self):
        return (Translate, self.identifier, self.language)

    def chain(self, next):
        if self.block:
            self.next = self.block[0]
            chain_block(self.block, next)
        else:
            self.next = next

        self.after = next

    def replace_next(self, old, new):
        Node.replace_next(self, old, new)

        if self.block and (self.block[0] is old):
            self.block.insert(0, new)

        if self.after is old:
            self.after = new

    def lookup(self) -> Node:
        return renpy.game.script.translator.lookup_translate(
            self.identifier,
            getattr(self, "alternate", None))

    def execute(self):

        if self.language is not None:
            next_node(self.next)
            raise Exception("Translation nodes cannot be run directly.")

        if self.identifier not in renpy.game.persistent._seen_translates:
            renpy.game.persistent._seen_translates.add(self.identifier)
            renpy.game.seen_translates_count += 1
            renpy.game.new_translates_count += 1

        next_node(self.lookup())

        renpy.game.context().translate_identifier = self.identifier
        renpy.game.context().alternate_translate_identifier = \
            getattr(self, "alternate", None)

    def predict(self) -> list[Node | None]:
        return [self.lookup()]

    def scry(self):
        rv = renpy.ast.Scry()
        rv._next = self.lookup()
        return rv

    def get_children(self, f):
        f(self)

        for i in self.block:
            i.get_children(f)

    def restructure(self, callback):
        return callback(self.block)


class TranslateSay(Say):
    """
    A node that combines a translate and a say statement.
    """

    translatable = True
    translation_relevant = True

    __slots__ = [
        "alternate",
        "language",
    ]

    alternate: str | None
    language: str | None

    def __init__(
        self,
        loc,
        who,
        what,
        with_,
        interact=True,
        attributes=None,
        arguments=None,
        temporary_attributes=None,
        identifier=None,
        language=None,
        alternate=None,
    ):
        super(TranslateSay, self).__init__(
            loc, who, what, with_, interact,
            attributes, arguments,
            temporary_attributes)

        self.identifier = identifier
        self.alternate = alternate
        self.language = language

    def diff_info(self):
        if self.language is None:
            return Say.diff_info(self)
        else:
            return (TranslateSay, self.identifier, self.language)

    def chain(self, next):
        Say.chain(self, next)
        self.after = next

    def replace_next(self, old, new):
        Say.replace_next(self, old, new)

        if self.after is old:
            self.after = new

    def lookup(self) -> Node:
        return renpy.game.script.translator.lookup_translate(
            self.identifier,
            getattr(self, "alternate", None))

    def execute(self):

        next_node(self.next)

        renpy.game.context().translate_identifier = self.identifier
        renpy.game.context().alternate_translate_identifier = \
            getattr(self, "alternate", None)

        if self.language is None:

            if self.identifier not in renpy.game.persistent._seen_translates:
                renpy.game.persistent._seen_translates.add(self.identifier)
                renpy.game.seen_translates_count += 1
                renpy.game.new_translates_count += 1

            # Potentially, jump to a translation.
            node = self.lookup()

            if (node is not None) and (node is not self):
                next_node(node)
                return

        # Otherwise, say the text.

        Say.execute(self)

        # Perform the equivalent of an endtranslate block.
        renpy.game.context().translate_identifier = None
        renpy.game.context().alternate_translate_identifier = None

    def predict(self) -> list[Node | None]:
        node = self.lookup()

        if node is None or node is self:
            return Say.predict(self)

        return [node]

    def scry(self):
        node = self.lookup()

        if node is None or node is self:
            return Say.scry(self)

        rv = renpy.ast.Scry()
        rv._next = self.next

        return rv


class EndTranslate(Node):
    """
    A node added implicitly after each translate block. It's responsible for
    resetting the translation identifier.
    """

    rollback = "never"

    def diff_info(self):
        return (EndTranslate, )

    def execute(self):
        next_node(self.next)

        renpy.game.context().translate_identifier = None
        renpy.game.context().alternate_translate_identifier = None


class TranslateString(Node):
    """
    A node used for translated strings.
    """

    translation_relevant = True

    __slots__ = [
        "language",
        "old",
        "new",
        "newloc",
    ]

    language: str
    old: str
    new: str
    newloc: tuple[str, int]

    def __init__(self, loc, language, old, new, newloc):
        super(TranslateString, self).__init__(loc)
        self.language = language

        self.old = old
        self.new = new
        self.newloc = newloc

    def diff_info(self):
        return (TranslateString, )

    def execute(self):
        next_node(self.next)

        newloc = getattr(self, "newloc", (self.filename, self.linenumber + 1))
        renpy.translation.add_string_translation(self.language, self.old, self.new, newloc)


class TranslatePython(Node):
    """
    Runs python code when changing the language.

    This is no longer generated, but is still run when encountered.
    """

    translation_relevant = True

    __slots__ = [
        'language',
        'code',
    ]

    language: str
    code: PyCode

    def __init__(self, loc, language, python_code):
        """
        @param code: A PyCode object.

        @param hide: If True, the code will be executed with its
        own local dictionary.
        """

        super(TranslatePython, self).__init__(loc)

        self.language = language
        self.code = PyCode(python_code, loc=loc, mode='exec')

    def diff_info(self):
        return (TranslatePython, self.code.source)

    def execute(self):
        next_node(self.next)

    # def early_execute(self):
    #    renpy.python.create_store(self.store)
    #    renpy.python.py_exec_bytecode(self.code.bytecode, self.hide, store=self.store)


class TranslateBlock(Node):
    """
    Runs a block of code when changing the language.
    """

    translation_relevant = True

    __slots__ = [
        'block',
        'language',
    ]

    block: list[Node]
    language: str

    def __init__(self, loc, language, block):
        super(TranslateBlock, self).__init__(loc)

        self.language = language
        self.block = block

    def get_children(self, f):
        f(self)

        for i in self.block:
            i.get_children(f)

    # We handle chaining specially. We want to chain together the nodes in
    # the block, but we want that chain to end in None, and we also want
    # this node to just continue on to the next node in normal execution.
    def chain(self, next):
        self.next = next
        chain_block(self.block, None)

    def execute(self):
        next_node(self.next)

    def restructure(self, callback):
        callback(self.block)


class TranslateEarlyBlock(TranslateBlock):
    """
    This is similar to the TranslateBlock, except it runs before deferred
    styles do.
    """
