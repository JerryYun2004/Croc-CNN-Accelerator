# Guard the one intended tap reduction using inventories, independent of KLayout.
module IOParentTapAudit
  def self.check(ok,message)
    raise 'Tap reduction audit: '+message unless ok
  end
  def self.close(a,b)
    a.is_a?(Numeric) && b.is_a?(Numeric) && (a-b).abs<=1e-9
  end
  def self.taps(inventory)
    inventory.fetch(:devices).select { |d| d.fetch(:model).downcase=='ptap1' }
  end
  def self.geometry(tap,area,perimeter)
    p=tap.fetch(:parameters)
    p.keys.map(&:to_s).sort==%w[A P] && close(p.fetch(:A),area) && close(p.fetch(:P),perimeter)
  end
  def self.connections(tap)
    t=tap.fetch(:terminals)
    check(t.keys.map(&:to_s).sort==%w[TIE WELL],'tap terminal set changed')
    [t.fetch(:TIE),t.fetch(:WELL)]
  end
  def self.before(inventory)
    keys=inventory.fetch(:nets)
    check(keys.all? { |k| k.is_a?(String) && !k.empty? } && keys.uniq.size==keys.size,
          'net keys must be nonempty and unique within the flattened circuit')
    endpoints=inventory.fetch(:devices).flat_map { |d| d.fetch(:terminals).values }
    endpoints+=inventory.fetch(:pins).map { |p| p[1] }
    check(endpoints.all? { |k| keys.include?(k) },'terminal or pin is outside the net inventory')
    ts=taps(inventory);check(ts.size==3,'expected three physical tap components')
    parent=ts.select { |t| connections(t)[0]=='vss' }
    child=ts.select { |t| connections(t)[0]=='iovss' }
    check(parent.size==1 && child.size==2,'expected one parent and two child tap components')
    check(geometry(parent.first,2.019,14.06),'parent tap geometry differs')
    child=child.sort_by { |t| t.fetch(:parameters).fetch(:A) }
    check(geometry(child[0],4.3112,25.36) && geometry(child[1],4.7192,27.76),
          'individual child tap geometry differs')
    wells=ts.map { |t| connections(t)[1] }
    check(wells.none?(&:nil?) && wells.uniq.size==1,'tap WELL nets differ or are disconnected')
    bulk=wells.first
    check(inventory.fetch(:pins).none? { |p| p[1]==bulk },'substrate aliases an external port')
    {parent:parent.first,children:child,bulk:bulk}
  end
  def self.after(pre,post)
    found=before(pre)
    check(pre.fetch(:pins)==post.fetch(:pins),'boundary pins changed')
    # KLayout Net has no numeric id method. The pre-audit requires unique
    # expanded names, so names are keys only within this flattened circuit.
    check(pre.fetch(:nets)==post.fetch(:nets),'net keys changed')
    untapped=lambda { |inv| inv.fetch(:devices).reject { |d| d.fetch(:model).downcase=='ptap1' }.sort_by { |d| d.fetch(:name) } }
    check(untapped.call(pre)==untapped.call(post),'a non-tap device changed')
    ts=taps(post);check(ts.size==2,'parallel reduction did not leave exactly two taps')
    parent=ts.select { |t| connections(t)==['vss',found[:bulk]] }
    child=ts.select { |t| connections(t)==['iovss',found[:bulk]] }
    check(parent.size==1 && parent.first==found[:parent],'parent tap changed')
    check(child.size==1,'combined child tap endpoints differ')
    area=found[:children].sum { |t| t.fetch(:parameters).fetch(:A) }
    perimeter=found[:children].sum { |t| t.fetch(:parameters).fetch(:P) }
    check(geometry(child.first,area,perimeter),'combined child A/P is not the sum')
    {status:'ONLY_CHILD_PARALLEL_TAPS_REDUCED',raw_taps:3,reduced_taps:2,
     child_area_sum:area,child_perimeter_sum:perimeter,non_taps_unchanged:true,
     pins_unchanged:true,nets_unchanged:true,net_joins:0}
  end
end
