from kivy.uix.button import Button
from kivy.factory import Factory

class ThemedButton(Button):
    pass

class AccentButton(Button):
    pass

# Auto-register with Factory
Factory.register('ThemedButton', ThemedButton)
Factory.register('AccentButton', AccentButton)