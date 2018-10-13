#!/usr/bin/env python3
import sys

import gi
import lzstring

gi.require_version('Gtk', '3.0')        # noqa
gi.require_version('WebKit2', '4.0')    # noqa
from gi.repository import Gio, Gtk, WebKit2


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
        <attribute name="action">app.save_as</attribute>
        <attribute name="label" translatable="yes">Save As</attribute>
      </item>
      <item>
        <attribute name="action">app.quit</attribute>
        <attribute name="label" translatable="yes">_Quit</attribute>
        <attribute name="accel">&lt;Primary&gt;q</attribute>
    </item>
    </section>
  </menu>
</interface>
"""

MATHPASTE_URL = 'https://purplemyst.github.io/mathpaste/'


class MathpasteWindow(Gtk.ApplicationWindow):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.webview = WebKit2.WebView()
        self.add(self.webview)
        self.webview.load_uri(MATHPASTE_URL)

    def show_math(self, math):
        url_part = lzstring.LZString().compressToEncodedURIComponent(math)
        self.webview.load_uri(MATHPASTE_URL + '#fullmath:' + url_part)
        self.webview.reload()   # no idea why this is needed

    def get_showing_math(self):
        url = self.webview.get_uri()
        if url == MATHPASTE_URL:
            return ''

        assert url.startswith(MATHPASTE_URL + '#fullmath:')
        url_part = url[len(MATHPASTE_URL + '#fullmath:'):]
        return lzstring.LZString().decompressFromEncodedURIComponent(url_part)


class MathpasteApplication(Gtk.Application):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.window = None
        self.current_filename = None

    # use this instead of setting self.current_filename when you want the
    # window title to update
    def set_current_filename(self, filename):
        self.current_filename = filename
        self.window.set_title("%s \N{em dash} MathPaste GTK" % filename)

    def do_startup(self):
        Gtk.Application.do_startup(self)    # no idea why super doesn't work

        for name in ['open', 'save', 'save_as', 'quit']:
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', getattr(self, 'on_' + name))
            self.add_action(action)

        builder = Gtk.Builder.new_from_string(MENU_XML, -1)
        self.set_app_menu(builder.get_object("app-menu"))

    def do_activate(self):
        if self.window is None:
            self.window = MathpasteWindow(application=self,
                                          title="MathPaste GTK")
            self.window.set_default_size(800, 600)
        self.window.show_all()
        self.window.present()

    def _create_dialog(self, title, action, ok_stock):
        dialog = Gtk.FileChooserDialog(
            title, self.window, action,
            (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
             ok_stock, Gtk.ResponseType.OK))

        filter = Gtk.FileFilter()
        filter.set_name("Text files")
        filter.add_mime_type("text/plain")
        dialog.add_filter(filter)

        filter = Gtk.FileFilter()
        filter.set_name("All files")
        filter.add_pattern("*")
        dialog.add_filter(filter)

        return dialog

    def on_open(self, action, param):
        dialog = self._create_dialog("Open Math", Gtk.FileChooserAction.OPEN,
                                     Gtk.STOCK_OPEN)
        if self.current_filename is not None:
            dialog.set_filename(self.current_filename)

        if dialog.run() == Gtk.ResponseType.OK:
            # TODO: error handling?
            self.set_current_filename(dialog.get_filename())
            with open(self.current_filename, 'r', encoding='utf-8') as file:
                self.window.show_math(file.read().rstrip('\n'))

        dialog.destroy()

    def on_save(self, action, param):
        if self.current_filename is None:
            return self.on_save_as(action, param)

        with open(self.current_filename, 'w') as file:
            file.write(self.window.get_showing_math() + '\n')

    def on_save_as(self, action, param):
        dialog = self._create_dialog("Save Math", Gtk.FileChooserAction.SAVE,
                                     Gtk.STOCK_SAVE)
        if self.current_filename is None:
            dialog.set_current_name("math.txt")
        else:
            dialog.set_filename(self.current_filename)

        if dialog.run() == Gtk.ResponseType.OK:
            self.set_current_filename(dialog.get_filename())
            assert self.current_filename is not None
            self.on_save(action, param)

        dialog.destroy()

    def on_quit(self, action, param):
        self.quit()


if __name__ == '__main__':
    MathpasteApplication().run(sys.argv)
