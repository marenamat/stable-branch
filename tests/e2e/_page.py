"""Playwright-compatible Page/Locator/expect API implemented on top of Selenium."""
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import StaleElementReferenceException


def _retry(fn, attempts=4, delay=0.15):
    """Re-run fn up to `attempts` times on StaleElementReferenceException."""
    for i in range(attempts):
        try:
            return fn()
        except StaleElementReferenceException:
            if i == attempts - 1:
                raise
            time.sleep(delay)


class Locator:
    def __init__(self, root_fn, css, text_filter=None, has_css=None, has_text=None, first_only=False):
        self._root_fn = root_fn
        self.css = css
        self.text_filter = text_filter
        self._has_css = has_css
        self._has_text = has_text
        self._first_only = first_only

    def _driver(self):
        from selenium.webdriver.remote.webdriver import WebDriver
        root = self._root_fn()
        return root if isinstance(root, WebDriver) else root.parent

    def _all(self):
        def _query():
            root = self._root_fn()
            els = root.find_elements(By.CSS_SELECTOR, self.css)
            if self.text_filter is not None:
                els = [e for e in els if self.text_filter in e.text]
            if self._has_css is not None:
                def _has_match(el):
                    sub = el.find_elements(By.CSS_SELECTOR, self._has_css)
                    if self._has_text:
                        sub = [s for s in sub if self._has_text in s.text]
                    return bool(sub)
                els = [e for e in els if _has_match(e)]
            if self._first_only:
                els = els[:1]
            return els
        try:
            return _retry(_query)
        except StaleElementReferenceException:
            return []

    def _one(self):
        els = self._all()
        if not els:
            raise Exception(f"No element: {self.css!r} text_filter={self.text_filter!r}")
        return els[0]

    @property
    def first(self):
        return Locator(self._root_fn, self.css, self.text_filter,
                       self._has_css, self._has_text, first_only=True)

    def filter(self, has_text=None, has=None):
        has_css = self._has_css
        has_txt = self._has_text
        if has is not None:
            has_css = has.css
            has_txt = has.text_filter
        return Locator(self._root_fn, self.css, has_text, has_css, has_txt)

    def locator(self, css, has_text=None):
        return Locator(lambda: self._one(), css, has_text)

    def click(self):
        _retry(lambda: self._one().click())

    def get_attribute(self, name):
        def _get():
            els = self._all()
            return els[0].get_attribute(name) if els else None
        return _retry(_get)

    def all_inner_texts(self):
        def _texts():
            return [e.text for e in self._all()]
        return _retry(_texts)

    def text_content(self):
        def _text():
            els = self._all()
            return els[0].text if els else None
        return _retry(_text)

    def count(self):
        return len(self._all())

    def is_displayed(self):
        def _disp():
            els = self._all()
            return bool(els) and els[0].is_displayed()
        try:
            return _retry(_disp)
        except StaleElementReferenceException:
            return False


class _Expect:
    def __init__(self, locator, timeout=5):
        self.loc = locator
        self.timeout = timeout

    def to_be_visible(self):
        WebDriverWait(self.loc._driver(), self.timeout).until(
            lambda _: self.loc.is_displayed()
        )

    def not_to_be_visible(self):
        WebDriverWait(self.loc._driver(), self.timeout).until(
            lambda _: not self.loc.is_displayed()
        )

    def to_have_count(self, n):
        WebDriverWait(self.loc._driver(), self.timeout).until(
            lambda _: self.loc.count() == n
        )


def expect(locator):
    return _Expect(locator)


class Page:
    """Playwright-compatible wrapper around a Selenium WebDriver."""

    def __init__(self, driver):
        self.driver = driver

    def locator(self, css, has_text=None):
        return Locator(lambda: self.driver, css, has_text)

    def wait_for_timeout(self, ms):
        time.sleep(ms / 1000)

    def wait_for_function(self, js_expr):
        WebDriverWait(self.driver, 5).until(
            lambda d: d.execute_script(f"return !!({js_expr})")
        )

    def evaluate(self, js):
        self.driver.execute_script(js)
