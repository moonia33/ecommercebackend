/* Cascading FK autocomplete: feature→feature_value, option_type→option_value.
 *
 * Django admin autocomplete is a Select2 AJAX widget; extra GET params are
 * forwarded to /admin/autocomplete/ where FeatureValueAdmin/OptionValueAdmin
 * read them in get_search_results(). This script wraps each "value" select's
 * ajax.data to inject the parent select's current value, and clears the value
 * when the parent changes.
 */
(function ($) {
  "use strict";

  if (!$) return;

  function patchAjax($val, $parent, param) {
    var s2 = $val.data("select2");
    if (!s2 || !s2.options) return false;
    var ajax = s2.options.get("ajax");
    if (!ajax || ajax.__cascade) return true;
    var origData = ajax.data;
    ajax.data = function (params) {
      var out = origData ? origData.apply(this, arguments) : (params || {});
      out[param] = $parent.val() || "";
      return out;
    };
    ajax.__cascade = true;
    s2.options.set("ajax", ajax);
    return true;
  }

  function parentFor($val, parentSuffix) {
    // Tabular inlines: same <tr>. Stacked: same .inline-related (kiekvienas
    // laukas yra atskirame .form-row, tad trūkus einame aukščiau).
    var sel = 'select[name$="-' + parentSuffix + '"]';
    var scopes = ["tr", ".inline-related", "fieldset"];
    for (var i = 0; i < scopes.length; i++) {
      var $c = $val.closest(scopes[i]);
      if ($c.length) {
        var $p = $c.find(sel).first();
        if ($p.length) return $p;
      }
    }
    return $();
  }

  function wire($val, parentSuffix, param) {
    var $parent = parentFor($val, parentSuffix);
    if (!$parent.length) return;
    // Patch now; if select2 isn't initialised yet, lazy-patch on opening
    // (fires before the first AJAX request).
    if (!patchAjax($val, $parent, param)) {
      $val.one("select2:opening", function () {
        patchAjax($val, $parent, param);
      });
    }
    $parent.on("change.cascade", function () {
      if ($val.val()) $val.val(null).trigger("change");
    });
  }

  function wireScope($scope) {
    $scope.find('select[name$="-feature_value"]').each(function () {
      wire($(this), "feature", "feature_id");
    });
    $scope.find('select[name$="-option_value"]').each(function () {
      wire($(this), "option_type", "option_type_id");
    });
  }

  $(function () {
    wireScope($(document));
    // Rows added via "Add another" in formsets.
    $(document).on("formset:added", function (e, $row) {
      wireScope($row);
    });
  });
})(window.django && window.django.jQuery);
