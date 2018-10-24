#!/usr/bin/env python3
# TODO:
#   - "do you want to save ur changes" things on opening a file and quitting
import base64
import collections
import enum
import json
import os
import sys
import traceback
import zipfile

import appdirs
import gi
from lzstring import LZString

gi.require_version('Gtk', '3.0')        # noqa
gi.require_version('WebKit2', '4.0')    # noqa
from gi.repository import GLib, Gio, Gtk, WebKit2


MATHPASTE_URL = 'https://akuli.github.io/mathpaste/'
SETTINGS_JSON = os.path.join(
    appdirs.user_config_dir('mathpaste-gtk'), 'settings.json')

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
        <attribute name="action">app.save</attribute>
        <attribute name="label" translatable="yes">Save</attribute>
        <attribute name="accel">&lt;Primary&gt;s</attribute>
      </item>
      <item>
        <attribute name="action">app.saveas</attribute>
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


class FileType(enum.Enum):
    ZIP = 1
    TEXT = 2


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


class MathpasteWindow(Gtk.ApplicationWindow):

    def __init__(self, app, **kwargs):
        super().__init__(application=app, **kwargs)
        self.app = app

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(box)

        self.webview = WebKit2.WebView()
        self.webview.load_uri(MATHPASTE_URL)
        box.pack_start(self.webview, True, True, 0)

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
        self.webview.set_zoom_level(app.config_dict['zoom'] / 100)
        self.zoom_scale.connect('value-changed', self._zoom_scale2webview)
        self.webview.connect('notify::zoom-level', self._zoom_webview2scale)

        for how2zoom in ['in', 'out', 'reset']:
            action = Gio.SimpleAction.new('zoom' + how2zoom, None)
            action.connect('activate', getattr(self, '_zoom_' + how2zoom))
            self.add_action(action)

        if os.environ.get('DEBUG', ''):
            (self.webview.get_settings().
             set_enable_write_console_messages_to_stdout(True))
            (self.webview.get_context().
             set_cache_model(WebKit2.CacheModel.DOCUMENT_VIEWER))

        # some funny code for communicating stuff from javascript to python
        self._callback_deque = collections.deque()
        self.webview.get_context().register_uri_scheme(
            'mathpaste-gtk-data', self._on_mathpaste_gtk_data)

    def _on_mathpaste_gtk_data(self, request):
        assert request.get_scheme() == 'mathpaste-gtk-data'
        assert request.get_uri().startswith('mathpaste-gtk-data://')

        lzstringed = request.get_uri()[len('mathpaste-gtk-data://'):]
        json_string = LZString().decompressFromEncodedURIComponent(lzstringed)
        python_object = json.loads(json_string)
        self._callback_deque.popleft()(python_object)

        empty_gstream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes(b''))
        request.finish(empty_gstream, 0)

    def show_math_and_image(self, math, image_string):
        def done_callback(webview, gtask):
            if gtask.had_error():
                # try again!
                print('showing math failed for some reason, trying again soon')
                GLib.timeout_add(200, self.show_math_and_image,
                                 math, image_string)

        # https://stackoverflow.com/a/10395491
        # mathpaste exposes a global mathpaste object with setMath and
        # getMath methods
        print('showing math and image:', math, image_string)
        self.webview.run_javascript(
            'mathpaste.setMathAndImage(%s, %s)' % (
                json.dumps(math), json.dumps(image_string)),
            None, done_callback)

    def get_showing_math_and_image(self, callback):
        """Get the current state of the mathpaste.

        On success, calls the callback with a dict from mathpaste's
        getMathAndImage() as an argument.
        """
        self._callback_deque.append(callback)
        self.webview.run_javascript(
            'window.location.href = "mathpaste-gtk-data://" + '
            'LZString.compressToEncodedURIComponent(JSON.stringify('
            'mathpaste.getMathAndImage()))')

    # these methods don't recurse infinitely for reasons that i can't explain
    def _zoom_webview2scale(self, webview, gparam):
        self.app.config_dict['zoom'] = round(webview.get_zoom_level() * 100)
        self.zoom_scale.set_value(self.app.config_dict['zoom'])

    def _zoom_scale2webview(self, scale):
        self.webview.set_zoom_level(scale.get_value() / 100)

    def _zoom_in(self, action, param):
        self.zoom_scale.set_value(self.zoom_scale.get_value() + 10)

    def _zoom_out(self, action, param):
        self.zoom_scale.set_value(self.zoom_scale.get_value() - 10)

    def _zoom_reset(self, action, param):
        self.zoom_scale.set_value(100)


class MathpasteApplication(Gtk.Application):

    def __init__(self):
        super().__init__(flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.config_dict = {
            'zoom': 100,
        }
        self.window = None

        # use set_current_file instead of setting these directly
        # these are None only when nothing has been opened yet
        self._current_filename = None
        self._current_filetype = None

    def set_current_file(self, filename, filetype):
        assert filename is not None
        assert filetype is not None
        self._current_filename = filename
        self._current_filetype = filetype

        if filetype == FileType.TEXT:
            format_string = "%s (text only) \N{em dash} MathPaste GTK"
        else:
            format_string = "%s \N{em dash} MathPaste GTK"

        self.window.set_title(format_string % filename)

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
        self.open_file(giofiles[0].get_path())

    def do_startup(self):
        Gtk.Application.do_startup(self)    # no idea why super doesn't work

        for name in ['open', 'save', 'saveas', 'quit']:
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', getattr(self, 'on_' + name))
            self.add_action(action)

        builder = Gtk.Builder.new_from_string(MENU_XML, -1)
        self.set_app_menu(builder.get_object("app-menu"))

    def do_activate(self):
        if self.window is None:
            self.window = MathpasteWindow(self, title="MathPaste GTK")
            self.window.set_default_size(800, 600)
        self.window.show_all()
        self.window.present()

    def _create_dialog(self, title, action, ok_stock):
        dialog = Gtk.FileChooserDialog(
            title, self.window, action,
            (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
             ok_stock, Gtk.ResponseType.OK))

        text_filter = Gtk.FileFilter()
        text_filter.set_name("Text files (no drawing)")
        text_filter.add_mime_type("text/plain")
        text_filter.mathpaste_filetype = FileType.TEXT   # yes, this works
        dialog.add_filter(text_filter)

        zip_filter = Gtk.FileFilter()
        zip_filter.set_name("Zip files (text and drawing)")
        zip_filter.add_mime_type('application/zip')
        zip_filter.mathpaste_filetype = FileType.ZIP
        dialog.add_filter(zip_filter)

        if action != Gtk.FileChooserAction.SAVE:
            all_filter = Gtk.FileFilter()
            all_filter.set_name("All files")
            all_filter.add_pattern("*")
            zip_filter.mathpaste_filetype = None
            dialog.add_filter(all_filter)

        # yes, adding custom attributes to gtk objects works
        dialog.filter2filetype = {
            text_filter: FileType.TEXT,
            zip_filter: FileType.ZIP,
        }
        dialog.filetype2filter = dict(map(reversed,
                                          dialog.filter2filetype.items()))

        if self._current_filename is not None:
            dialog.set_filename(self._current_filename)
            dialog.set_filter(dialog.filetype2filter[self._current_filetype])

        return dialog

    def _show_open_save_error(self, open_or_save, filename, message):
        dialog = Gtk.MessageDialog(
            self.window, 0, Gtk.MessageType.ERROR, Gtk.ButtonsType.OK,
            "Cannot %s '%s'" % (open_or_save, filename))
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def open_file(self, path):
        try:
            filetype, math, image_string = read_mathpaste_file(path)
        except OSError as e:
            self._show_open_save_error("open", str(e))
            return
        except UnicodeError:
            self._show_open_save_error(
                "open", "The text doesn't seem to be encoded in UTF-8.")
            return
        except zipfile.BadZipfile:
            self._show_open_save_error(
                "open", "The zip file seems to be damaged.")
            return
        except NotAMathPasteGtkZipFileError:
            self._show_open_save_error(
                "open", "The zip file is not compatible with MathPaste GTK.")
            return
        except Exception:
            traceback.print_exc()
            self._show_open_save_error("open", "An unexpected error occurred.")
            return

        self.window.show_math_and_image(math, image_string)
        self.set_current_file(path, filetype)

    def on_open(self, action, param):
        dialog = self._create_dialog("Open Math", Gtk.FileChooserAction.OPEN,
                                     Gtk.STOCK_OPEN)
        if dialog.run() == Gtk.ResponseType.OK:
            self.open_file(dialog.get_filename())

        dialog.destroy()

    def on_save(self, action, param):
        if self._current_filename is None:
            self.on_saveas(action, param)

        def callback(dictionary):
            print('saving:', dictionary)
            try:
                write_mathpaste_file(
                    self._current_filename, self._current_filetype,
                    dictionary['math'], dictionary['imageString'],
                    dictionary['imageDataUrl'])
            except OSError as e:
                self._show_open_save_error("save", str(e))
                return
            except Exception:
                traceback.print_exc()
                self._show_open_save_error(
                    "save", "An unexpected error occurred.")
                return

            # user-friendliness :)
            if (self._current_filetype == FileType.TEXT and
                    dictionary['imageString']):
                dialog = Gtk.MessageDialog(
                    self.window, 0, Gtk.MessageType.WARNING,
                    Gtk.ButtonsType.OK, "Your drawing wasn't saved")
                dialog.format_secondary_text(
                    "If you want to also save the drawing, don't choose the "
                    '"Text files" filetype in the "Save As" dialog.')
                dialog.run()
                dialog.destroy()

        self.window.get_showing_math_and_image(callback)

    def on_saveas(self, action, param):
        dialog = self._create_dialog("Save Math", Gtk.FileChooserAction.SAVE,
                                     Gtk.STOCK_SAVE)
        dialog.set_do_overwrite_confirmation(True)
        if dialog.run() == Gtk.ResponseType.OK:
            self.set_current_file(dialog.get_filename(),
                                  dialog.filter2filetype[dialog.get_filter()])
            self.on_save(action, param)

        dialog.destroy()

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
