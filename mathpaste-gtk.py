#!/usr/bin/env python3
import base64
import enum
import functools
import itertools
import json
import os
import re
import sys
import traceback
import urllib.parse
import webbrowser
import zipfile

import appdirs
import gi

gi.require_version('Gtk', '3.0')        # noqa
gi.require_version('WebKit2', '4.0')    # noqa
from gi.repository import GLib, Gio, Gtk, WebKit2


DEBUG_MODE = bool(os.environ.get('DEBUG', ''))

# for developing mathpaste-gtk, you can also run mathpaste locally, see
# mathpaste's README for instructions
# note that these must end with a slash!
#MATHPASTE_URL = 'http://localhost:8000/'
MATHPASTE_URL = 'https://akuli.github.io/mathpaste/'

SETTINGS_JSON = os.path.join(
    appdirs.user_config_dir('mathpaste-gtk'), 'settings.json')

PASTE_URL_REGEX = r'https://\w+\.github\.io/mathpaste/#((?:fullmath|saved):.+)'

# https://python-gtk-3-tutorial.readthedocs.io/en/latest/application.html
MENU_XML = """
<?xml version="1.0" encoding="UTF-8"?>
<interface>
  <menu id="app-menu">
    <section>
      <item>
        <attribute name="action">app.open</attribute>
        <attribute name="label" translatable="yes">_Open</attribute>
        <attribute name="accel">&lt;Primary&gt;o</attribute>
      </item>
      <item>
        <attribute name="action">app.openurl</attribute>
        <attribute name="label" translatable="yes">Open a MathPaste URL</attri\
bute>
        <attribute name="accel">&lt;Primary&gt;&lt;Shift&gt;o</attribute>
      </item>
      <item>
        <attribute name="action">win.save</attribute>
        <attribute name="label" translatable="yes">Save</attribute>
        <attribute name="accel">&lt;Primary&gt;s</attribute>
      </item>
      <item>
        <attribute name="action">win.saveas</attribute>
        <attribute name="label" translatable="yes">Save As</attribute>
        <attribute name="accel">&lt;Primary&gt;&lt;Shift&gt;s</attribute>
      </item>
    </section>
    <section>
      <item>
        <attribute name="action">win.zoomin</attribute>
        <attribute name="label" translatable="yes">Zoom In</attribute>
        <attribute name="accel">&lt;Primary&gt;plus</attribute>
      </item>
      <item>
        <attribute name="action">win.zoomout</attribute>
        <attribute name="label" translatable="yes">Zoom Out</attribute>
        <attribute name="accel">&lt;Primary&gt;minus</attribute>
      </item>
      <item>
        <attribute name="action">win.zoomreset</attribute>
        <attribute name="label" translatable="yes">Reset Zoom</attribute>
        <attribute name="accel">&lt;Primary&gt;0</attribute>
      </item>
    </section>
    <section>
      <item>
        <attribute name="action">app.quit</attribute>
        <attribute name="label" translatable="yes">_Quit</attribute>
        <attribute name="accel">&lt;Primary&gt;q</attribute>
      </item>
    </section>
  </menu>
</interface>
"""


# the enum values are Gtk file filters, this way converting between FileType
# enums and the file filters is easy, but debugging is also easier because
# enum has a much better repr
#
# some_filetype.value is the Gtk.FileFilter
# FileType(file_filter) is the filetype of the given file filter
class FileType(enum.Enum):
    TEXT = Gtk.FileFilter()
    TEXT.set_name("Text files (no drawing)")
    TEXT.add_mime_type("text/plain")

    ZIP = Gtk.FileFilter()
    ZIP.set_name("Zip files (text and drawing)")
    ZIP.add_mime_type('application/zip')


# the name of this exception feels like java
class NotAMathPasteGtkZipFileError(Exception):
    """Raised when attempting to open a zip file that isn't a mathpaste zip."""


def read_mathpaste_file(filename):
    """Read any file saved by mathpaste.

    Returns (filetype, math, image_string) and raises:
    * OSError if reading the file fails.
    * UnicodeError if the file's text is not valid UTF-8.
    * zipfile.BadZipFile if the file seems to be a zip file but it isn't.
    * NotAMathPasteGtkZipFileError if the file is a zip file, but it doesn't
      contain the things that mathpaste-gtk needs.
    """
    with open(filename, 'rb') as file:
        magic = file.read(4)

    # these are the magic bytes of a zip file
    # https://en.wikipedia.org/wiki/List_of_file_signatures
    if magic == b'PK\x03\x04':
        with zipfile.ZipFile(filename) as zip_:
            if 'math.txt' not in zip_.namelist():
                raise NotAMathPasteGtkZipFileError(
                    "'%s' doesn't seem to be a MathPaste zip file" % filename)

            # mathpaste zips also contain drawing.png, but we don't need it
            with zip_.open('math.txt', 'r') as file:
                math = file.read().decode('utf-8')

            # not all mathpaste zips contain a drawing
            if 'drawing-data.txt' in zip_.namelist():
                with zip_.open('drawing-data.txt', 'r') as file:
                    image_string = file.read().decode('ascii')
            else:
                image_string = ''

            return (FileType.ZIP, math, image_string)

    else:
        # assume text file, no picture
        with open(filename, 'r', encoding='utf-8') as file:
            return (FileType.TEXT, file.read(), '')


def write_mathpaste_file(filename, filetype, math,
                         image_string, image_dataurl):
    """Saves the math to a file, and maybe a picture too depending on filetype.

    Raises OSError if writing the file fails.
    """
    if filetype == FileType.TEXT:
        with open(filename, 'w', encoding='utf-8') as file:
            file.write(math)
    elif filetype == FileType.ZIP:
        with zipfile.ZipFile(filename, 'w') as zip_:
            zip_.writestr('math.txt', math.encode('utf-8'))

            # an empty image string means that nothing has been drawn
            if image_string:
                zip_.writestr('drawing-data.txt', image_string.encode('ascii'))

                prefix, b64data = image_dataurl.split(',')
                assert prefix == 'data:image/png;base64'
                zip_.writestr('drawing.png', base64.b64decode(b64data))
    else:
        raise NotImplementedError("unknown filetype: " + repr(filetype))


class MathpasteView(WebKit2.WebView):
    """WebView subclass with useful methods and other stuffs.

    Try to keep all WebKit code in this class.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.load_uri(MATHPASTE_URL)

        if DEBUG_MODE:
            # show console.log and friends in terminal
            self.get_settings().set_enable_write_console_messages_to_stdout(
                True)       # lol pep8

            # don't cache anything
            self.get_context().set_cache_model(
                WebKit2.CacheModel.DOCUMENT_VIEWER)

        # disallow navigating to anywhere on the internet
        self.connect('decide-policy', self._webbrowser_link_opener)

        # some funny code for communicating stuff between javascript and python
        self._callback_dict = {}    # {id: function}
        self._callback_id_counter = itertools.count()
        self.get_context().register_uri_scheme(
            'mathpaste-gtk-data', self._handle_data_from_javascript)

        self.change_callback = lambda: None
        self._run_javascript_until_succeeds('''
        mathpaste.addChangeCallback(() => {
            /*
            this isn't done with mathpaste-gtk-data:// urls because that didn't
            work for some weird reason:

              * at first it seemed to work fine, but then i typed ' into it
                and it froze
              * i added a 250ms timeout that ran the callback, and typing '
                worked fine, but sometimes it froze randomly otherwise, e.g.
                when adding tt in front of "some text", so that it became
                tt"some text"

            gtk's change callback runs only when the title actually changes, so
            setting it to 'modified' every time is not sufficient
            */
            if (document.title === 'modified') {
                document.title = 'modified2';
            } else {
                document.title = 'modified';
            }
        });

        // mathpaste-gtk has its own ways to handle storaging the math
        mathpaste.setUseLocalStorage(false);
        ''')
        self.connect('notify::title', self._on_title_changed)

    def _run_javascript_until_succeeds(self, js):
        def done_callback(view, gtask):
            if gtask.had_error():
                # this happens when this is called early and mathpaste hasn't
                # loaded fully yet
                if DEBUG_MODE:
                    print('running a javascript failed, trying again soon')
                GLib.timeout_add(200, self._run_javascript_until_succeeds, js)

        self.run_javascript(js, None, done_callback)

    def _webbrowser_link_opener(self, view, decision, decision_type):
        if decision_type == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if not (uri == MATHPASTE_URL or
                    uri.startswith(MATHPASTE_URL + '#') or
                    uri.startswith('mathpaste-gtk-data://')):
                if DEBUG_MODE:
                    print('opening external link in web browser:', uri)

                webbrowser.open(uri)
                decision.ignore()

    def _handle_data_from_javascript(self, request):
        assert request.get_scheme() == 'mathpaste-gtk-data'
        assert request.get_uri().startswith('mathpaste-gtk-data://')

        data_part_of_uri = request.get_uri()[len('mathpaste-gtk-data://'):]
        id_, encoded_uri_component = data_part_of_uri.split(',', 1)
        json_string = urllib.parse.unquote(encoded_uri_component)
        python_object = json.loads(json_string)
        self._callback_dict.pop(int(id_))(python_object)

        empty_gstream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes(b''))
        request.finish(empty_gstream, 0)

    def _on_title_changed(self, *junk):
        if self.get_title() in {'modified', 'modified2'}:
            self.change_callback()

    def show_math_and_image(self, math, image_string):
        if DEBUG_MODE:
            print('showing math and image:', math, image_string)

        # https://stackoverflow.com/a/10395491
        self._run_javascript_until_succeeds(
            'mathpaste.setMathAndImage(%s, %s)' % (
                json.dumps(math), json.dumps(image_string)))

    def show_math_from_window_location_hash(self, hash_, done_callback):
        def javascript_done_callback(view, gtask):
            if gtask.had_error():
                print("show_math_from_window_location_hash: javascript error")
            else:
                done_callback()

        self.run_javascript('''
        window.location.hash = %s;
        mathpaste.loadMathFromWindowDotLocationDotHash();
        ''' % json.dumps(hash_), None, javascript_done_callback)

    def get_showing_math_and_image(self, callback):
        """Get the current state of the mathpaste.

        On success, calls the callback with a dict from mathpaste's
        getMathAndImage() as an argument.
        """
        id_ = next(self._callback_id_counter)
        self._callback_dict[id_] = callback
        self.run_javascript(
            'window.location.href = "mathpaste-gtk-data://%d," + '
            'encodeURIComponent(JSON.stringify('
            'mathpaste.getMathAndImage()))' % id_)


class MathpasteWindow(Gtk.ApplicationWindow):
    """A window with a MathpasteView in it.

    Right now there are never multiple MathpasteWindows at a time, but I
    might do that in the future, so all the stuff specific to each
    window should be in this class.
    """

    def __init__(self, app, **kwargs):
        super().__init__(application=app, **kwargs)
        self.app = app

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(box)

        self.view = MathpasteView()
        box.pack_start(self.view, True, True, 0)

        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.pack_start(bottom_bar, False, False, 0)

        bottom_bar.add(Gtk.Label("Zoom %: "))
        self.zoom_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 10, 300, 10)
        self.zoom_scale.props.width_request = 200
        bottom_bar.add(self.zoom_scale)

        # for rolling mouse wheel on the slider
        self.zoom_scale.get_adjustment().set_page_increment(10)

        self.zoom_scale.set_value(app.config_dict['zoom'])
        self.view.set_zoom_level(app.config_dict['zoom'] / 100)
        self.zoom_scale.connect('value-changed', self._zoom_scale2view)
        self.view.connect('notify::zoom-level', self._zoom_view2scale)

        for name in ['zoomin', 'zoomout', 'zoomreset', 'save', 'saveas']:
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', getattr(self, 'on_' + name))
            self.add_action(action)

        # use set_current_file and set_saved instead of setting these directly
        # these are None only when nothing has been opened yet
        self._current_filename = None
        self._current_filetype = None
        self._saved = True
        self.view.change_callback = functools.partial(self.set_saved, False)
        self._update_title()

        self.connect('delete-event', self._on_user_wants_to_close_the_window)

    def set_current_file(self, filename, filetype):
        assert filename is not None
        assert filetype is not None
        self._current_filename = filename
        self._current_filetype = filetype
        self._update_title()

    def set_saved(self, boolean):
        self._saved = boolean
        self._update_title()

    def _update_title(self):
        parts = []

        if self._current_filename is None:
            parts.append('New math')
        else:
            parts.append(self._current_filename)

        paren_part = []
        if self._current_filetype == FileType.TEXT:
            paren_part.append('text only')
        if not self._saved:
            paren_part.append('modified')
        if paren_part:
            parts.append('(%s)' % ', '.join(paren_part))

        self.set_title("%s \N{em dash} MathPaste GTK" % ' '.join(parts))

    def open_file(self, path):
        error = functools.partial(self._show_open_or_save_error, "open", path)

        try:
            filetype, math, image_string = read_mathpaste_file(path)
        except OSError as e:
            error(str(e))
            return
        except UnicodeError:
            error("The text doesn't seem to be encoded in UTF-8.")
            return
        except zipfile.BadZipfile:
            error("The zip file seems to be damaged.")
            return
        except NotAMathPasteGtkZipFileError:
            error("The zip file is not compatible with MathPaste GTK.")
            return
        except Exception:
            traceback.print_exc()
            error("An unexpected error occurred.")
            return

        self.view.show_math_and_image(math, image_string)
        self.set_current_file(path, filetype)
        self.set_saved(True)

    def open_math_url(self, url):
        self.view.show_math_from_window_location_hash(
            re.fullmatch(PASTE_URL_REGEX, url).group(1),
            functools.partial(self.set_saved, True))

    def save(self, callback):
        """Save the file, calling save_as if needed.

        Runs callback() on success, but doesn't run it if user cancels.
        """
        if self._current_filename is None:
            self.save_as(callback)
            return

        def callback_for_view(dictionary):
            if DEBUG_MODE:
                print('saving:', dictionary)

            error = functools.partial(
                self._show_open_or_save_error, "save", self._current_filename)
            try:
                write_mathpaste_file(
                    self._current_filename, self._current_filetype,
                    dictionary['math'], dictionary['imageString'],
                    dictionary['imageDataUrl'])
            except OSError as e:
                error(str(e))
                return
            except Exception:
                traceback.print_exc()
                error("An unexpected error occurred.")
                return

            self.set_saved(True)

            # user-friendliness :)
            if (self._current_filetype == FileType.TEXT and
                    dictionary['imageString']):
                dialog = Gtk.MessageDialog(
                    self, 0, Gtk.MessageType.WARNING,
                    Gtk.ButtonsType.OK, "Your drawing wasn't saved")
                dialog.format_secondary_text(
                    "If you want to save the drawing too, don't choose the "
                    '"Text files" filetype in the "Save As" dialog.')
                dialog.run()
                dialog.destroy()

            callback()

        self.view.get_showing_math_and_image(callback_for_view)

    def save_as(self, callback):
        """Ask the user where to save the file, and save it.

        Runs callback() on success, but doesn't run it if user cancels.
        """
        dialog = self.create_file_dialog(
            "Save Math", Gtk.FileChooserAction.SAVE, Gtk.STOCK_SAVE)
        dialog.set_do_overwrite_confirmation(True)
        if dialog.run() == Gtk.ResponseType.OK:
            self.set_current_file(dialog.get_filename(),
                                  FileType(dialog.get_filter()))
            self.save(callback)
        dialog.destroy()

    def on_save(self, action, param):
        self.save(lambda: None)

    def on_saveas(self, action, param):
        self.save_as(lambda: None)

    def save_if_user_wants_to(self, callback):
        """Save if the user wants to.

        This is called when a new math is opened or the user tries to close the
        window.

        The callback should work so that running callback() means "go ahead, do
        the thing", and not running it means "no, don't do anything". In more
        details:
        * If there's nothing to save, the callback is called.
        * Otherwise. the user is asked whether they want to save the math.
            * If the user cancels, the callback is not called.
            * If the user says no, the callback is called.
            * If the user says yes, the file is saved.
                * If saving the file succeeds, the callback is called.
                * If saving the file fails, the callback is not called.
        """
        if self._saved:
            callback()
            return

        if self._current_filename is None:
            text = "Do you want to save the math?"
        else:
            text = ("Do you want to save your changes to '%s'?"
                    % self._current_filename)

        dialog = Gtk.MessageDialog(self, 0, Gtk.MessageType.QUESTION,
                                   Gtk.ButtonsType.NONE, text)
        dialog.add_button(Gtk.STOCK_YES, Gtk.ResponseType.YES)
        dialog.add_button(Gtk.STOCK_NO, Gtk.ResponseType.NO)
        dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        response = dialog.run()
        dialog.destroy()

        if response == Gtk.ResponseType.YES:
            self.save(callback)
        elif response == Gtk.ResponseType.NO:
            callback()
        # assume that any other response means canceling, could be e.g. user
        # closing the window

    def _on_user_wants_to_close_the_window(self, window, event):
        # calling self.destroy() doesn't recurse, because this callback runs
        # only when the user tries to close the window
        self.save_if_user_wants_to(self.destroy)

        # prevent gtk from closing the window now to let save_if_user_wants_to
        # do its thing
        return True

    # these methods don't recurse infinitely for reasons that i can't explain
    def _zoom_view2scale(self, view, gparam):
        self.app.config_dict['zoom'] = round(view.get_zoom_level() * 100)
        self.zoom_scale.set_value(self.app.config_dict['zoom'])

    def _zoom_scale2view(self, scale):
        self.view.set_zoom_level(scale.get_value() / 100)

    def on_zoomin(self, action, param):
        self.zoom_scale.set_value(self.zoom_scale.get_value() + 10)

    def on_zoomout(self, action, param):
        self.zoom_scale.set_value(self.zoom_scale.get_value() - 10)

    def on_zoomreset(self, action, param):
        self.zoom_scale.set_value(100)

    def _show_open_or_save_error(self, open_or_save, filename, message):
        dialog = Gtk.MessageDialog(
            self, 0, Gtk.MessageType.ERROR, Gtk.ButtonsType.OK,
            "Cannot %s '%s'" % (open_or_save, filename))
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def create_file_dialog(self, title, action, ok_stock):
        dialog = Gtk.FileChooserDialog(
            title, self, action,
            (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
             ok_stock, Gtk.ResponseType.OK))

        for filetype in FileType:
            dialog.add_filter(filetype.value)

        if action != Gtk.FileChooserAction.SAVE:
            all_filter = Gtk.FileFilter()
            all_filter.set_name("All files")
            all_filter.add_pattern("*")
            dialog.add_filter(all_filter)

        if self._current_filename is not None:
            dialog.set_filename(self._current_filename)
            dialog.set_filter(self._current_filetype.value)

        return dialog


class MathpasteApplication(Gtk.Application):

    def __init__(self):
        super().__init__(flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.config_dict = {
            'zoom': 100,
        }
        self.window = None

    def do_startup(self):
        Gtk.Application.do_startup(self)    # no idea why super doesn't work

        for name in ['open', 'openurl', 'quit']:
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', getattr(self, 'on_' + name))
            self.add_action(action)

        builder = Gtk.Builder.new_from_string(MENU_XML, -1)
        self.set_app_menu(builder.get_object("app-menu"))

    def do_open(self, giofiles, *junk):
        if len(giofiles) != 1:
            print("%s: can only open exactly 1 file at a time, not %d"
                  % (sys.argv[0], len(giofiles)), file=sys.stderr)
            sys.exit(2)

        # seems like do_open() always needs to do this, otherwise the app exits
        # without doing anything
        self.activate()

        # i didn't feel like figuring out how to read the file with gio, this
        # works fine
        self.window.open_file(giofiles[0].get_path())

    def do_activate(self):
        if self.window is None:
            self.window = MathpasteWindow(self)
            self.window.set_default_size(800, 600)
        self.window.show_all()
        self.window.present()

    def on_open(self, action, param):
        def callback():
            dialog = self.window.create_file_dialog(
                "Open Math", Gtk.FileChooserAction.OPEN, Gtk.STOCK_OPEN)
            if dialog.run() == Gtk.ResponseType.OK:
                self.window.open_file(dialog.get_filename())
            dialog.destroy()

        self.window.save_if_user_wants_to(callback)

    def on_openurl(self, action, param):
        def callback():
            dialog = Gtk.Dialog(
                "Open MathPaste URL", self.window, 0,
                (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                 Gtk.STOCK_OPEN, Gtk.ResponseType.OK))
            dialog.set_default_size(500, 150)

            ok_button = dialog.get_widget_for_response(Gtk.ResponseType.OK)
            ok_button.set_sensitive(False)

            def on_entry_content_changed(entry):
                if re.fullmatch(PASTE_URL_REGEX, entry.get_text()):
                    ok_button.set_sensitive(True)
                    ok_button.set_tooltip_text(None)
                elif not entry.get_text():
                    ok_button.set_sensitive(False)
                    ok_button.set_tooltip_text("Please paste a URL first.")
                else:
                    ok_button.set_sensitive(False)
                    ok_button.set_tooltip_text(
                       "'%s' is not a valid MathPaste URL." % entry.get_text())

            entry = Gtk.Entry()
            entry.connect('changed', on_entry_content_changed)
            on_entry_content_changed(entry)
            entry.connect('activate',
                          lambda entry: dialog.response(Gtk.ResponseType.OK))

            content = dialog.get_content_area()
            content.pack_start(Gtk.Label("Paste the URL here:"),
                               False, False, 0)
            content.pack_start(entry, False, False, 0)
            dialog.show_all()

            response = dialog.run()
            if response == Gtk.ResponseType.OK and ok_button.get_sensitive():
                self.window.open_math_url(entry.get_text())

            dialog.destroy()

        self.window.save_if_user_wants_to(callback)

    def on_quit(self, action, param):
        self.quit()

    def read_config(self):
        with open(SETTINGS_JSON, 'r', encoding='utf-8') as file:
            self.config_dict.update(json.load(file))

    def write_config(self):
        os.makedirs(os.path.dirname(SETTINGS_JSON), exist_ok=True)
        with open(SETTINGS_JSON, 'w', encoding='utf-8') as file:
            json.dump(self.config_dict, file)


def main():
    app = MathpasteApplication()
    try:
        app.read_config()
    except FileNotFoundError:
        pass
    except Exception:
        traceback.print_exc()

    try:
        app.run(sys.argv)
    finally:
        app.write_config()


if __name__ == '__main__':
    main()
