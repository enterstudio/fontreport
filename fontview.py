# coding = utf-8
"""Font internals reporting tool.

This command-line tool is able to generate various reports about the internals
of the given TrueType or OpenType font, such as:
  - Supported unicode characters
  - Supported glyphs

Reports are generated in PDF format and contain font-specific rendering of
the glyphs supported by the font.

"""
import os
import re
import subprocess
import sys
import unicodedata

from fontTools.ttLib import TTFont


class Glyph(object):
  """Representation of a single glyph.

  Contains glyph properties collected from different tables of a font and
  queried by report classes.
  """

  def __init__(self, name):
    self.name = name
    self.advanceWidth = None
    self.lsb = None
    self.classDef = 0
    self.sequences = None
    self.alternates = None
    self.chars = []
    self.index = -1


class FontFile(object):
  """Representation of font metadata.

  Provides high-level API on top of FontTools library that is used by
  report generators.
  """
  NAME_CODES = {'Copyright': 0, 'Family': 1, 'Subfamily': 2,
                'Full Name': 4, 'Version': 5, 'PostScrpt Name': 6,
                'Trademark': 7, 'Manufacturer': 8, 'Designer': 9,
                'Description': 10, 'Vendor URL': 11, 'Designer URL': 12,
                'License': 13, 'License URL': 14, 'Sample Text': 19}

  def __init__(self, filename):
    self.filename = filename
    self.ttf = TTFont(filename, fontNumber=-1, lazy=False)
    self._names = {}
    self.chars = {}
    self._glyphsmap = {}
    self.glyphs = []
    self.features = {}
    self.caretList = {}
    self.substitutes = set()
    self.caretList = {}
    self._ParseNames()
    self._ParseCmap()
    self._ParseGSUB()
    self._ParseGlyphs()

  def _ParseNames(self):
    if 'name' in self.ttf:
      for name in self.ttf['name'].names:
        if name.isUnicode():
          text = name.string.decode('utf-16be')
        else:
          text = name.string.decode('latin1')
        if name.nameID not in self._names:
          self._names[name.nameID] = text

  def GetTables(self):
    return sorted(self.ttf.reader.keys())

  def GetTitle(self):
    title = self.GetName('Full Name')
    if not title:
      title = self.GetName('Family') + ' ' + self.GetName('Subfamily')
    return title

  def GetAuthor(self):
    author = self.GetName('Designer')
    manufacturer = self.GetName('Manufacturer')
    if author and manufacturer and author != manufacturer:
      return '%s (%s)' % (author, manufacturer)
    elif author:
      return author
    elif manufacturer:
      return manufacturer
    else:
      return 'Author is not set'

  def GetFeaturesByTable(self):
    mapping = {}
    for key, scripts in self.features.iteritems():
      feature, tables = key
      for table in tables:
        if table not in mapping:
          mapping[table] = set()
        mapping[table].add((feature, tuple(sorted(scripts))))
    return mapping

  def _ParseCmap(self):
    if 'cmap' in self.ttf:
      for table in self.ttf['cmap'].tables:
        if table.isUnicode():
          for code, name in table.cmap.items():
            self.chars[code] = name

  def _ParseGSUB(self):
    if 'GSUB' not in self.ttf:
      return

    scripts = [None] * self.ttf['GSUB'].table.FeatureList.FeatureCount
    # Find scripts defined in a font
    for script in self.ttf['GSUB'].table.ScriptList.ScriptRecord:
      if script.Script.DefaultLangSys:
        for idx in script.Script.DefaultLangSys.FeatureIndex:
          scripts[idx] = script.ScriptTag
      for lang in script.Script.LangSysRecord:
        for idx in lang.LangSys.FeatureIndex:
          scripts[idx] = script.ScriptTag + '-' + lang.LangSysTag

    # Find all featrures defined in a font
    for idx, feature in enumerate(self.ttf['GSUB'].table.FeatureList.FeatureRecord):
      key = (feature.FeatureTag, tuple(feature.Feature.LookupListIndex))
      if key not in self.features:
        self.features[key] = set()
      self.features[key].add(scripts[idx])

    for idx, lookup in enumerate(self.ttf['GSUB'].table.LookupList.Lookup):
      for sub in lookup.SubTable:
        if sub.LookupType == 1:
          for k, v in sub.mapping.iteritems():
            self.substitutes.add(((k,), ((v,),), idx, 1))
        elif sub.LookupType == 2:
          for k, v in sub.mapping.iteritems():
            self.substitutes.add(((k,),  (tuple(v),), idx, 1))
        elif sub.LookupType == 3:
          for k, v in sub.alternates.iteritems():
            self.substitutes.add(((k,), tuple((x,) for x in v), idx, 3))
        elif sub.LookupType == 4:
          for key, value in sub.ligatures.iteritems():
            for component in value:
              sequence = tuple([key] + component.Component)
              glyph = component.LigGlyph
              self.substitutes.add((sequence, ((glyph,),), idx, 4))
        else:
          print 'Lookup table %d: type %s not yet supported.' % (idx, sub.LookupType)

  def _ParseGlyphs(self):
    """Fetch available glyphs."""
    classDefs = {}
    classNames = {2: 'ligature', 3: 'mark', 4: 'component'}
    metrics = {}
    if 'GDEF' in self.ttf:
      classDefs = self.ttf['GDEF'].table.GlyphClassDef.classDefs
      fontCaretList = self.ttf['GDEF'].table.LigCaretList
      if fontCaretList:
        carets = [tuple(str(x.Coordinate) for x in y.CaretValue)
                  for y in fontCaretList.LigGlyph]
        self.caretList = dict(zip(fontCaretList.Coverage.glyphs, carets))

    if 'hmtx' in self.ttf:
      metrics = self.ttf['hmtx'].metrics

    for idx, name in enumerate(self.ttf.getGlyphOrder()):
      glyph = Glyph(name)
      glyph.index = idx
      glyph.advanceWidth, glyph.lsb = metrics.get(name, [None, None])
      glyph.classDef = classDefs.get(name, 0)
      glyph.className = classNames.get(glyph.classDef, None)
      self.glyphs.append(glyph)
      self._glyphsmap[name] = glyph
    for k, v in self.chars.iteritems():
      try:
        self._glyphsmap[v].chars.append(k)
      except KeyError:
        print '%s is mapped to non-existent glyph %s' % (k, v)

  def GetName(self, name, default=None):
    return self._names.get(self.NAME_CODES[name], default)

  def GetNames(self):
    return ['%d: %s' % (k, v) for k, v in sorted(self._names.iteritems())]

  def GetGlyph(self, name):
    return self._glyphsmap[name]


class Report(object):
  """Base class for report generator classes."""

  def __init__(self, font):
    self.font = font

  def Report(self, xetex):
    if xetex:
      return self.Xetex()
    else:
      return self.Plaintext()

  def Plaintext(self):
    pass

  def Xetex(self):
    body = self.XetexBody()
    if body:
      body = self.TETEX_HEADER + body + self.TETEX_FOOTER
    return body


class UnicodeCoverageReport(Report):
  """Report font unicode coverage."""

  TETEX_HEADER = r'''
    \definecolor{missing}{gray}{.95}
    \begin{longtable}[l]{|r|l|p{0.6\textwidth}|}
    \hline
    \endhead
    \hline
    \endfoot
  '''
  TETEX_FOOTER = r'\end{longtable}'

  NAME = 'Unicode Coverage'

  def Plaintext(self):
    data = ''
    for code, name in sorted(self.font.chars.iteritems()):
      try:
        uniname = unicodedata.name(unichr(code))
      except ValueError:
        uniname = ''
      data += '  U+%04d %-30s %s\n' % (code, name, uniname)
    return data

  def XetexBody(self):
    data = ''
    prevcode = 0
    for code in sorted(self.font.chars):
      try:
        uniname = unicodedata.name(unichr(code))
      except ValueError:
        uniname = ''
      if code - prevcode > 1:
        gaps = len([x for x in  xrange(prevcode + 1, code)
                    if unicodedata.category(unichr(x))[0] != 'C'])
        if gaps:
          data += '\\rowcolor{missing}\\multicolumn{3}{|c|}{\\small %d codepoints not mapped} \\\\\n' % (gaps)
      prevcode = code
      data += '\\texttt{%04X} & {\\customfont\\symbol{%d}} & {\\small %s}\\\\\n' % (code, code, uniname)
    return data


class GlyphsReport(Report):
  NAME = 'Glyphs'

  TETEX_HEADER = r'''
    \definecolor{ligature}{RGB}{255, 255, 200}
    \definecolor{mark}{RGB}{255, 200, 255}
    \definecolor{component}{RGB}{200, 255, 255}
    \begin{longtable}[l]{|r|l|l|r|r|r|p{.2\textwidth}|}
    \hline
    \rowcolor{header}
    Index & Glyph & Name & Adv. Width & lsb & Class & Chars\\
    \hline
    \endhead
    \hline
    \endfoot
  '''

  TETEX_FOOTER = r'\end{longtable}'

  def Plaintext(self):
    data = ''
    for glyph in self.font.glyphs:
      data += '%6d %-30s %6d %6d %3d\n' % (
          glyph.index, glyph.name, glyph.advanceWidth,
          glyph.lsb, glyph.classDef)
    return data

  def XetexBody(self):
    data = ''
    uni = {}
    for code, name in self.font.chars.iteritems():
      if name not in uni:
        uni[name] = []
      uni[name].append(code)
    for glyph in self.font.glyphs:
      if glyph.className:
        data += '\\rowcolor{%s}\n' % glyph.className
      if glyph.name in uni:
        chars = ', '.join('u%04X' % x for x in glyph.chars)
      else:
        chars = ''
      data += '%d & %s & %s & %d & %d & %d & %s\\\\\n' % (
          glyph.index, TexGlyph(glyph), TexEscape(glyph.name),
          glyph.advanceWidth, glyph.lsb, glyph.classDef, chars)
    return data


class LigaturesReport(Report):
  """Report ligatures."""
  TETEX_HEADER = r'''
    \begin{longtable}[l]{|l|l|l|p{.5\textwidth}|}
    \hline
    \rowcolor{header}
    Glyph & Caret Positions & Sequences \\
    \hline
    \endhead
    \hline
    \endfoot
  '''
  TETEX_FOOTER = r'\end{longtable}'

  NAME = 'Ligatures with Carets'

  def Plaintext(self):
    data = ''
    for glyph, caretList in self.font.caretList.iteritems():
      data += '%-10s\t%s\t%s\n' % (
          glyph, ', '.join(caretList), '-')
    return data

  def XetexBody(self):
    data = ''
    for glyph, caretList in self.font.caretList.iteritems():
      coords = ', '.join(str(x) for x in caretList)
      data += '%s(%s) & %s & %s \\\\\n' % (
          TexGlyph(self.font.GetGlyph(glyph)), TexEscape(glyph),
          coords, '-' )
    return data


class SubstitutionsReport(Report):
  """Report GPOS substitutions."""
  TETEX_HEADER = r'''
    \begin{longtable}[l]{|c|c|p{.7\textwidth}|}
    \hline
    \rowcolor{header}
    Table & Feature & Substitution  \\
    \hline
    \endhead
    \hline
    \endfoot
  '''
  TETEX_FOOTER = r'\end{longtable}'

  NAME = 'GPOS Substitutions'

  def Plaintext(self):
    data = ''
    for table, features, src, dest in self.GetTableItems():
      data += '%4d\t%-20s\t%s\n' % (
          table,
          ', '.join(features),
          ' '.join(src) + ' -> ' +
          ', '.join(' '.join(y) for y in dest))
    return data

  def XetexBody(self):
    features_mapping = self.font.GetFeaturesByTable()
    data = ''
    for table, features, src, dest in self.GetTableItems():
      sequence = ' '.join('%s(%s)' % (
          TexGlyph(self.font.GetGlyph(x)), TexEscape(x)) for x in src)
      alternates = ', '.join(' '.join('%s(%s)' % (
          TexGlyph(self.font.GetGlyph(x)), TexEscape(x)) for x in y) for y in dest)
      data += '%d & %s & %s$\\rightarrow$%s \\\\\n' % (
          table, ', '.join(features), sequence, alternates)
    return data

  def GetTableItems(self):
    features_mapping = self.font.GetFeaturesByTable()
    for src, dest, table, kind in sorted(self.font.substitutes, key=lambda x:(x[2], x[0])):
      if table in features_mapping:
        features = sorted(set(k for k, v in features_mapping[table]))
      else:
        features = ()
      yield (table, features, src, dest)


class FeaturesReport(Report):
  """Report OpenType features."""
  TETEX_HEADER = r'''
    \begin{longtable}[l]{|l|l|l|l|}
    \hline
    \rowcolor{header}
    Feature & Description & Scripts & Lookup Tables \\
    \hline
    \endhead
    \hline
    \endfoot
  '''
  TETEX_FOOTER = r'\end{longtable}'

  NAME = 'OpenType Features'

  def Plaintext(self):
    data = ''
    for key, scripts in sorted(self.font.features.iteritems()):
      feature, tables = key
      scriptlist = ', '.join(x for x in scripts if x)
      data += '%4s\t%s\t%s\t%s\n' % (feature, 'N/A', scriptlist,
                                 ', '.join(str(x) for x in tables))
    return data

  def XetexBody(self):
    data = ''
    for key, scripts in sorted(self.font.features.iteritems()):
      feature, tables = key
      scriptlist = ', '.join(x for x in scripts if x)
      data += '%s & %s & %s & %s  \\\\\n' % (
          feature, 'N/A', scriptlist, ', '.join(str(x) for x in tables))
    return data


class SummaryReport(Report):
  TETEX_HEADER = r'''
    \begin{tabular}[l]{|l|r|}
    \hline
  '''

  TETEX_FOOTER = r'\hline\end{tabular}'

  NAME = 'Summary'

  def Plaintext(self):
    return '\n'.join('%20s: %d' % x for x in self._GetData())

  def XetexBody(self):
    data = '\n'.join('%s & %d \\\\' % x for x in self._GetData())
    return data

  def _GetData(self):
    glyphs = self.font.glyphs
    count = {2: 0, 3: 0, 4: 0}
    for x in glyphs:
      count[x.classDef] = count.get(x.classDef, 0) + 1
    return (('Unicode characters', len(self.font.chars)),
            ('Glyphs', len(glyphs)),
            ('Ligature glyphs', count[2]),
            ('Mark glyphs', count[3]),
            ('Component glyphs', count[4]))


class Envelope(Report):
  """Reporting entry point.

  Combines all reports into a single document.
  """
  TETEX_HEADER = r'''
    \documentclass[10pt]{article}
    \usepackage{fontspec}
    \usepackage{longtable}
    \usepackage[top=.25in, bottom=.25in, left=.5in, right=.5in]{geometry}
    \usepackage[table]{xcolor}
    \definecolor{header}{gray}{.9}
    \usepackage{hyperref}
    \hypersetup{
        colorlinks,
        citecolor=black,
        filecolor=black,
        linkcolor=black,
        urlcolor=black
    }
    \begin{document}
  '''

  TETEX_FOOTER = r'\end{document}'

  FONT_TEMPLATE = r'''
    \newfontface\customfont[Path = %s/, Color = 0000AA]{%s}
  '''

  KNOWN_REPORTS = (SummaryReport, UnicodeCoverageReport,
                   GlyphsReport, FeaturesReport,
                   LigaturesReport, SubstitutionsReport)

  def Plaintext(self):
    data = ''
    for report in self.KNOWN_REPORTS:
      try:
        content = report(self.font).Plaintext()
        if content:
          data += report.NAME.upper() + '\n' + content + '\n\n'
      except AttributeError:
        pass
    return data

  def XetexBody(self):
    data = self.FONT_TEMPLATE % os.path.split(self.font.filename)
    data += '\\title{%s}\n' % TexEscape(self.font.GetTitle())
    data += '\\author{%s}\n' % TexEscape(self.font.GetAuthor())
    data += '\\renewcommand\\today{%s}' % TexEscape(
        self.font.GetName('Version', 'Unknown version'))
    data += '\\maketitle\n\\tableofcontents\n'
    for report in self.KNOWN_REPORTS:
      try:
        content = report(self.font).Xetex()
        if content:
          data += '\\section{%s}\n%s\n' % (report.NAME, content)
      except AttributeError:
        pass
    return data


def TexGlyph(glyph):
  return '{\\customfont\\XeTeXglyph %d}' % glyph.index


def TexEscape(name):
  return re.sub(r'([_#&{}\[\]])', r'\\\1', name)


def main(argv):
  if len(argv) == 3:
    infile = argv[1]
    outfile = argv[2]
  elif len(argv) == 2:
    infile = argv[1]
    outfile = None
  else:
    print 'Usage: %s infile [outfile]' % argv[0]
    sys.exit(-1)

  font = FontFile(infile)
  envelope = Envelope(font)
  if outfile:
    name, ext = os.path.splitext(outfile)
    tofile = outfile
    xetex = False
    if ext in ('.pdf', '.tex'):
      xetex = True
      if ext == '.pdf':
        tofile = name + '.tex'

    with open(tofile, 'w') as f:
      f.write(envelope.Report(xetex).encode('utf-8'))
    if ext == '.pdf':
      # Call twice to let longtable package calculate
      # column width correctly
      subprocess.check_call(['xelatex', tofile])
      subprocess.check_call(['xelatex', tofile])
  else:
    print envelope.Report(False).encode('utf-8')


if __name__ == '__main__':
  main(sys.argv)
