"""
/***************************************************************************
 Total Viewshed Calculator Plugin
 Compute total viewshed from Digital Elevation Models
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""


def classFactory(iface):
    """Load Total Viewshed Calculator class from the provided interface."""
    from .total_viewshed_plugin import TotalViewshedPlugin
    return TotalViewshedPlugin(iface)
